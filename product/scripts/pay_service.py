# -*- coding: utf-8 -*-
"""T2 支付自动激活【服务端核心】: 订单 + 易支付 adapter + 付款回调自动 mint token + 轮询查单。

流程(全自动分发):
  客户端选档 → POST /pay/create{device,tier} → 服务端建订单 + 签一个易支付支付URL 返回 →
  客户端浏览器打开付款 → 付款后【易支付平台 GET/POST 回调 notify_url】→ 服务端验签 → 按档
  按公版档位自动 mint 限期 token(绑定设备 30/90/365 天;可漫游 30/90/365 天)→ 记 订单↔token →
  客户端【轮询 /pay/query】拿 token
  → 自动激活(免手填)。

设计:
- provider-agnostic: 订单流程与 mint 复用 activation_core.mint_token; 支付平台相关只在 EpayAdapter
  (签名+验签+URL, 默认【彩虹易支付标准】; 换 provider 只改/加 adapter)。
- token uid = 订单号(out_trade_no), 便于对账 + 撤销定位。标准档首次激活绑定设备;可漫游档换机时
  由 /activate 重绑当前设备。公版不再销售永久 token,但 activation_core 仍兼容既有永久 token。
- 纯 stdlib(与 collector 一致, 可直接在服务器跑)。金额用字符串(避免浮点), tier→金额在服务端权威
  (客户端只传 tier, 不传金额, 防篡改低价)。

⚠️ 防转卖(作者顾虑)当前【只记录不做】: 设备绑定已挡单 token 多机复用; 批量倒卖的限购/实名以后再说。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

# 公版档位 = 服务端权威(客户端只传 key)。amount 必须与支付后台一致。
# 普通档绑定首次设备; roaming 档换机激活时重绑。365 天是最高档,不再提供永久销售项。
TIERS: dict[str, dict[str, Any]] = {
    "m30": {"amount": "12.88", "name": "BidKing 30天·绑定设备", "days": 30, "roaming": False},
    "d90": {"amount": "28.88", "name": "BidKing 90天·绑定设备", "days": 90, "roaming": False},
    "y365": {"amount": "68.88", "name": "BidKing 365天·绑定设备·含私有大数据", "days": 365, "roaming": False},
    "r30": {"amount": "19.99", "name": "BidKing 30天·可漫游", "days": 30, "roaming": True},
    "r90": {"amount": "39.99", "name": "BidKing 90天·可漫游", "days": 90, "roaming": True},
    "r365": {"amount": "89.99", "name": "BidKing 365天·可漫游·含私有大数据", "days": 365, "roaming": True},
}


def _mint_default(uid: str, secret: bytes, iat: Optional[int] = None,
                  exp: Optional[int] = None, roaming: bool = False) -> str:
    from activation_core import mint_token
    return mint_token(uid, secret, iat=iat, exp=exp, roaming=roaming)


class EpayAdapter:
    """彩虹易支付标准 adapter: 页面跳转下单(submit)+ 回调验签。

    签名规则(彩虹标准): 参数按 key 升序, 剔除 sign/sign_type/空值, 拼 k=v&k=v(【不 urlencode】),
    末尾直接接商户 KEY(无分隔), 取 MD5 小写。回调按同规则验 sign。
    换 provider(如虎皮椒 appid/appsecret 不同签名)= 换一个 adapter, PayService 不变。
    """

    def __init__(self, gateway: str, pid: str, key: str) -> None:
        self.gateway = gateway.rstrip("/")
        self.pid = str(pid)
        self.key = key

    def sign(self, params: dict[str, Any]) -> str:
        items = sorted(
            (k, str(v)) for k, v in params.items()
            if k not in ("sign", "sign_type") and v not in ("", None)
        )
        raw = "&".join(f"{k}={v}" for k, v in items) + self.key
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def build_pay_url(self, *, out_trade_no: str, amount: str, name: str,
                      notify_url: str, return_url: str = "", channel: str = "") -> str:
        """构造页面跳转支付 URL(浏览器打开, 用户在易支付收银台付款; channel 空=收银台自选支付宝/微信)。"""
        params: dict[str, Any] = {
            "pid": self.pid,
            "out_trade_no": out_trade_no,
            "notify_url": notify_url,
            "return_url": return_url,
            "name": name,
            "money": amount,
            "sign_type": "MD5",
        }
        if channel:  # alipay | wxpay; 空则收银台让用户选
            params["type"] = channel
        params["sign"] = self.sign(params)
        return f"{self.gateway}/submit.php?" + urlencode(params)

    def verify_notify(self, params: dict[str, Any]) -> bool:
        sign = str(params.get("sign", ""))
        return bool(sign) and hmac.compare_digest(self.sign(params), sign)  # 常数时间比较


class PayService:
    """订单流程 + 自动 mint。orders 落 JSONL(append)+ 内存索引(out_trade_no -> order)。"""

    def __init__(self, *, adapter: EpayAdapter, secret: bytes, orders_path: Path,
                 base_url: str, mint_fn: Callable[..., str] = _mint_default) -> None:
        self.adapter = adapter
        self.secret = secret
        self.orders_path = Path(orders_path)
        self.base_url = base_url.rstrip("/")
        self.mint_fn = mint_fn
        self._lock = threading.Lock()
        self._orders: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.orders_path.exists():
            return
        for ln in self.orders_path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("out_trade_no"):
                self._orders[o["out_trade_no"]] = o   # 后写覆盖前(状态推进)

    def _persist(self, order: dict[str, Any]) -> None:
        with self.orders_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(order, ensure_ascii=False) + "\n")

    def _gen_otn(self, device: str, tier: str, now: int) -> str:
        """生成不含设备明文、可在同秒多次建单的订单号。

        档位参与不可逆前缀，64-bit 随机 nonce 解决同设备/同秒/同档重复；生成时在
        self._lock 内与已持久化订单索引查重，因此即使极小概率 nonce 碰撞也不会覆盖。
        """
        prefix = hashlib.sha1(f"{device}|{tier}".encode("utf-8")).hexdigest()[:8]
        for _ in range(32):
            out_trade_no = f"BK{now}{prefix}{secrets.token_hex(8)}"
            if out_trade_no not in self._orders:
                return out_trade_no
        raise RuntimeError("cannot allocate unique out_trade_no")

    def create_order(self, *, device: str, tier: str, channel: str = "",
                     now: Optional[int] = None) -> dict[str, Any]:
        if tier not in TIERS:
            return {"ok": False, "error": "bad_tier"}
        if not device:
            return {"ok": False, "error": "no_device"}
        now = int(time.time()) if now is None else int(now)
        t = TIERS[tier]
        with self._lock:
            otn = self._gen_otn(device, tier, now)
            order = {
                "out_trade_no": otn, "device": device, "tier": tier,
                "amount": t["amount"], "status": "pending", "token": None,
                "created": now, "paid_at": None,
            }
            self._orders[otn] = order
            self._persist(order)
        pay_url = self.adapter.build_pay_url(
            out_trade_no=otn, amount=t["amount"], name=t["name"],
            notify_url=self.base_url + "/pay/notify",
            return_url=self.base_url + "/pay/return", channel=channel,
        )
        return {"ok": True, "out_trade_no": otn, "pay_url": pay_url, "amount": t["amount"]}

    def handle_notify(self, params: dict[str, Any], *, now: Optional[int] = None) -> str:
        """易支付回调: 验签 + 金额/状态核对 + 自动 mint。返回给平台的响应体("success" 表示已处理)。"""
        now = int(time.time()) if now is None else int(now)
        if not self.adapter.verify_notify(params):
            return "fail"   # 验签不过 = 伪造/错 key
        if str(params.get("trade_status", "")) not in ("TRADE_SUCCESS", "SUCCESS"):
            return "success"  # 非成功态(如 pending), 应答避免平台重推, 但不 mint
        otn = str(params.get("out_trade_no", ""))
        with self._lock:
            order = self._orders.get(otn)
            if order is None:
                return "fail"   # 未知订单
            # 金额必须与建单时一致(防篡改)
            if str(params.get("money", "")) != str(order["amount"]):
                return "fail"
            if order["status"] == "paid" and order.get("token"):
                return "success"  # 幂等: 平台可能重推, 已 mint 直接应答
            tier = TIERS[order["tier"]]
            exp = now + int(tier["days"]) * 86400
            token = self.mint_fn(
                otn, self.secret, iat=now, exp=exp, roaming=bool(tier["roaming"])
            )
            order.update(status="paid", token=token, paid_at=now)
            self._persist(order)
        return "success"

    def query_order(self, out_trade_no: str) -> dict[str, Any]:
        """客户端轮询: 返回订单状态 + (已付则)token。"""
        with self._lock:
            order = self._orders.get(out_trade_no)
        if order is None:
            return {"ok": False, "error": "not_found"}
        return {
            "ok": True, "status": order["status"],
            "token": order.get("token") if order["status"] == "paid" else None,
            "tier": order["tier"],
        }
