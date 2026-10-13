"""Presentation-only membership actions; this module never grants entitlement."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any


def renewal_available(state: Any, *, diagnostic: bool = False) -> bool:
    if type(state) is not dict:
        return False
    capability = state.get("membership_card_capability")
    return bool(
        not diagnostic
        and state.get("run_full") is True and state.get("mode") == "paid"
        and type(state.get("plan_kind")) is str
        and state.get("plan_kind") in {"30d", "90d", "365d"}
        and type(capability) is dict
        and all(type(capability.get(key)) is int for key in ("schema_version", "pet_quota_schema_version"))
        and capability == {"schema_version": 1, "pet_quota_schema_version": 2}
    )


def renewal_rule_text(state: Any, *, diagnostic: bool = False) -> str:
    if diagnostic:
        return "已有 token 可继续激活；本包不提供新权益开通。"
    if renewal_available(state):
        return "同类型未激活新卡可续费，剩余时间保留；已用卡或不同类型请选“更换使用”。"
    return "旧 token 继续有效。自助续费需服务端支持；仅更换 token 不会合并会员时长。"


SWITCH_CONFIRMATION = (
    "将本机改为使用这张 token，不合并两份会员。原会员保持原到期时间，不会暂停计时。\n\n"
    "如果你是想给当前会员加时，请取消并选择“续费”。\n是否继续更换？"
)


@dataclass(frozen=True)
class CardFeedback:
    succeeded: bool
    text: str
    allow_switch: bool = False
    confirmation_pending: bool = False


def renewal_feedback(result: Any) -> CardFeedback:
    ack = getattr(result, "card_redemption", None)
    status = ack.get("status") if type(ack) is dict else None
    status = status if type(status) is str else None
    if (getattr(result, "activated", False) is True
            and getattr(result, "mode", "") == "paid"
            and type(ack) is dict and type(ack.get("schema_version")) is int and ack["schema_version"] == 1
            and ack.get("committed") is True and status in {"redeemed", "replayed"}
            and type(ack.get("credited_seconds")) is int
            and ack["credited_seconds"] in {30 * 86400, 90 * 86400, 365 * 86400}):
        text = (f"续费成功 · 已增加 {ack['credited_seconds'] // 86400} 天"
                if status == "redeemed" else "已确认此前续费 · 未重复加时")
        expiry = ack.get("membership_exp")
        if type(expiry) is int and expiry > 0:
            try:
                text += "\n会员到期：" + time.strftime("%Y-%m-%d %H:%M", time.localtime(expiry))
            except (ValueError, OverflowError, OSError):
                pass
        if status == "redeemed" and ack.get("pet_quota_added_seconds") == 43200:
            text += " · 猫猫配额增加 12 小时"
        return CardFeedback(True, text)
    if type(ack) is dict and ack.get("committed") is False:
        reasons = {
            "switch_required_permissions": "绑定卡与可换绑卡不能合并。可选择“更换使用”，原会员保留。",
            "switch_required_used_card": "这张卡已使用，不能再次加时；可选择“更换使用”。",
            "switch_required_permanent": "这张卡不参与时长合并；可选择“更换使用”。",
            "switch_required_device_activation": "请先在本机使用原会员 token 完成换绑，再续费；新卡未消耗。",
            "unsupported_expired_account": "当前会员已到期，可选择“更换使用”激活新卡。",
            "unsupported_current_membership": "当前会员不支持直接合并；可选择“更换使用”。",
            "unsupported_plan": "该套餐不支持自动合并；可选择“更换使用”。",
            "renewal_not_configured": "服务端尚未开启自助续费，新卡未消耗；请联系作者核对。",
            "invalid_new_card": "新卡未通过验证，请重新复制完整 token；当前 token 未更换。",
            "new_card_revoked": "新卡暂不可用，请联系作者核对；当前 token 未更换。",
            "card_unavailable": "这张卡无法用于本次续费，请联系作者核对。",
        }
        can_switch = status in {"switch_required_permissions", "switch_required_used_card",
                                "switch_required_permanent", "unsupported_expired_account",
                                "unsupported_current_membership", "unsupported_plan"}
        return CardFeedback(False, reasons.get(status, "本次未续费，请核对新卡或联系作者。"), can_switch)
    if getattr(result, "reason", "") in {"revoked", "device_revoked", "upgrade_required", "expired", "device_mismatch", "bad_token"}:
        return CardFeedback(False, "当前会员验证未通过，请按主界面提示处理；未自动更换 token。")
    return CardFeedback(False, "续费结果尚未确认，请用同一张卡重试；不要重复购买。", confirmation_pending=True)
