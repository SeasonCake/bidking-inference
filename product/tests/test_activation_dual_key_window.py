"""双密钥窗口与显式客户端安全代际切换门。

背景: 0.2.6 及更早的 /activate 走明文 HTTP 下发 master_key -> 主密钥必须视为已泄露 -> 轮换。
但旧包的数据是用旧 key 加密的, 服务端单 key 直接换新 = 所有旧包解不开 = 全员失活。
故默认按 client_version 发 key，维持部署兼容。正式切代时由 min_client_security_epoch 明确拒绝
缺少 HTTPS-only 协议字段的旧客户端，且拒绝响应绝不携带任何 key。
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import activation_core as ac  # noqa: E402
import activation_server as asv  # noqa: E402
from bidking_lab.live import version_check as vc  # noqa: E402


OLD_KEY = "OLDKEYb64AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
NEW_KEY = "NEWKEYb64BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
SECRET = b"test-activation-secret"


class _ExplosiveVersionStr(str):
    calls = 0

    def explode(self, *args, **kwargs):
        type(self).calls += 1
        raise AssertionError("str subclass protocol must not be called")

    __str__ = explode
    strip = explode
    lower = explode
    casefold = explode
    __eq__ = explode


class _ExplosiveVersionProtocol:
    calls = 0

    def explode(self, *args, **kwargs):
        type(self).calls += 1
        raise AssertionError("non-str protocol must not be called")

    __str__ = explode
    strip = explode
    lower = explode
    casefold = explode
    __eq__ = explode
    __bool__ = explode
    __int__ = explode


def _svc(
    tmp_path: Path,
    *,
    v2: str | None,
    v2_min: str = "0.2.7",
    min_epoch: int = 0,
    min_version: str | None = None,
) -> asv.ActivationService:
    return asv.ActivationService(
        secret=SECRET,
        master_key_b64=OLD_KEY,
        master_key_v2_b64=v2,
        v2_min_version=v2_min,
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
        trial_hours=1.0,
        min_client_security_epoch=min_epoch,
        min_client_version=min_version,
    )


def _paid_token(uid: str = "u-dual-1") -> str:
    return ac.mint_token(uid, SECRET)


# --------------------------------------------------------------------------- #
# 版本比较(边界)
# --------------------------------------------------------------------------- #
def test_version_ge_handles_hotfix_suffix_and_missing():
    # -hotfix 后缀不该把 0.2.6-hotfix1 抬成 >= 0.2.7
    assert asv._version_ge("0.2.6-hotfix1", "0.2.7") is False
    assert asv._version_ge("0.2.7", "0.2.7") is True
    assert asv._version_ge("0.2.7-hotfix1", "0.2.7") is True
    assert asv._version_ge("0.2.10", "0.2.7") is True   # 数字段比较, 非字典序
    assert asv._version_ge("0.3.0", "0.2.7") is True
    # 老客户端根本不带这个字段 -> None/空 -> 必须判为"低于" -> 发旧 key
    assert asv._version_ge(None, "0.2.7") is False
    assert asv._version_ge("", "0.2.7") is False
    # 段数不同(0.3 vs 0.2.7)右侧补 0
    assert asv._version_ge("0.3", "0.2.7") is True


def test_release_version_order_distinguishes_candidate_and_hotfix_channels():
    assert asv._release_version_ge("0.2.9-r1", "0.2.9") is True
    assert asv._release_version_ge("0.2.9-r2", "0.2.9-r1") is True
    assert asv._release_version_ge("0.2.9-hotfix1", "0.2.9-r99") is True
    assert asv._release_version_ge("0.2.9-r99", "0.2.9-hotfix1") is False
    assert asv._release_version_ge("0.2.9-hotfix2", "0.2.9-hotfix1") is True
    assert asv._release_version_ge("0.2.10", "0.2.9-hotfix99") is True


@pytest.mark.parametrize(
    "value",
    (
        "0.2.9",
        "0.2.9-r1",
        "0.2.9-r99",
        "0.2.9-hotfix1",
        "0.2.9-HOTFIX2",
        "0.3.0-hotfix2-r1",
        "0.3.1-hotfix3",
        "0.3.1-hotfix4",
        "0.3.1-hotfix5",
        "0.3.2",
        "0.3.2-hotfix1",
        "0.3.2-hotfix2",
        "0.3.2-hotfix3",
        "0.3.2-hotfix4",
        "0.3.3",
        "0.3.3-hotfix1",
        "0.3.3-hotfix2",
        None,
        True,
        "",
        "0.2",
        "0.2.9.1",
        "v0.2.9",
        "0.2.9-preview-hotfix1",
    ),
)
def test_release_version_parser_matches_client_semantics(value):
    assert asv._release_vtuple(value) == vc._vtuple(value)


@pytest.mark.parametrize(
    "value",
    (
        None,
        "",
        "0.2",
        "0.2.9-preview1",
        "v0.2.9",
        "0.2.9-r",
        "０.３.１-hotfix２",
        "٠.٣.١-hotfix٢",
        "०.३.१-hotfix२",
        " 0.3.1-hotfix2",
        "0.3.1-hotfix2\t",
        "0.3.1-hotfix2\n",
        "0.3.1-hotfİx2",
        "0.3.1-hotfıx2",
        [],
        True,
    ),
)
def test_release_version_parser_rejects_missing_or_malformed_values(value):
    assert asv._release_vtuple(value) is None


def test_release_version_parser_rejects_all_non_ascii_decimal_digits():
    digits = tuple(
        chr(codepoint)
        for codepoint in range(0x110000)
        if unicodedata.category(chr(codepoint)) == "Nd"
        and not 0x30 <= codepoint <= 0x39
    )
    assert len(digits) >= 640
    for digit in digits:
        for value in (
            f"{digit}.3.1-hotfix2",
            f"0.{digit}.1-hotfix2",
            f"0.3.{digit}-hotfix2",
            f"0.3.1-r{digit}",
            f"0.3.1-hotfix{digit}",
            f"0.3.1-hotfix2-r{digit}",
        ):
            assert asv._release_vtuple(value) is None
            assert asv._release_vtuple(value) == vc._vtuple(value)


def test_release_version_parser_rejects_protocol_objects_without_calls():
    _ExplosiveVersionStr.calls = 0
    _ExplosiveVersionProtocol.calls = 0
    values = (
        _ExplosiveVersionStr("0.3.1-hotfix2"),
        _ExplosiveVersionProtocol(),
    )
    for value in values:
        assert asv._release_vtuple(value) is None
        assert asv._release_version_ge(value, "0.3.1") is False
    assert _ExplosiveVersionStr.calls == 0
    assert _ExplosiveVersionProtocol.calls == 0


# --------------------------------------------------------------------------- #
# ⭐向后兼容铁律: 轮换后老客户端仍拿旧 key(旧包不失活)
# --------------------------------------------------------------------------- #
def test_rotated_server_still_gives_old_key_to_legacy_client(tmp_path):
    """0.2.6 客户端(请求里没有 client_version)在【已轮换】的服务端上必须仍拿到旧 key。
    这是轮换不打死已发出包的唯一保证。"""
    svc = _svc(tmp_path, v2=NEW_KEY)
    code, body = svc.handle_activate({"device": "dev-legacy", "token": _paid_token("u-legacy")})
    assert code == 200 and body["ok"] is True
    assert body["master_key"] == OLD_KEY   # ← 不是 NEW_KEY


def test_rotated_server_gives_old_key_to_026_hotfix1(tmp_path):
    """带版本但版本低于 0.2.7 的客户端(0.2.6-hotfix1)也拿旧 key。"""
    svc = _svc(tmp_path, v2=NEW_KEY)
    code, body = svc.handle_activate(
        {"device": "dev-026", "token": _paid_token("u-026"), "client_version": "0.2.6-hotfix1"}
    )
    assert code == 200
    assert body["master_key"] == OLD_KEY


def test_rotated_server_gives_new_key_to_027(tmp_path):
    """0.2.7+(走 HTTPS 的第一个版本)拿轮换后的新 key。"""
    svc = _svc(tmp_path, v2=NEW_KEY)
    code, body = svc.handle_activate(
        {"device": "dev-027", "token": _paid_token("u-027"), "client_version": "0.2.7"}
    )
    assert code == 200
    assert body["master_key"] == NEW_KEY


# --------------------------------------------------------------------------- #
# ⭐零风险增量: 未轮换(没放 v2 文件)时行为与轮换前完全等价
# --------------------------------------------------------------------------- #
def test_unrotated_server_always_old_key_even_for_new_client(tmp_path):
    """未部署 master_key_v2.txt 时, 连 0.2.7 客户端也拿旧 key —— 即可以先安全上线这段代码,
    择期再轮换; 代码上线本身不改变任何行为。"""
    svc = _svc(tmp_path, v2=None)
    for cv in (None, "0.2.6-hotfix1", "0.2.7", "0.3.0"):
        body_in = {"device": f"dev-{cv}", "token": _paid_token(f"u-{cv}")}
        if cv:
            body_in["client_version"] = cv
        code, body = svc.handle_activate(body_in)
        assert code == 200, (cv, body)
        assert body["master_key"] == OLD_KEY, f"未轮换却发了非旧key: {cv}"


def test_empty_v2_file_content_treated_as_unrotated(tmp_path):
    """v2 文件存在但内容是空白 -> 当作未轮换(而不是发一把空 key 把新客户端打死)。"""
    svc = _svc(tmp_path, v2="   \n  ")
    code, body = svc.handle_activate(
        {"device": "dev-x", "token": _paid_token("u-x"), "client_version": "0.2.7"}
    )
    assert code == 200
    assert body["master_key"] == OLD_KEY


# --------------------------------------------------------------------------- #
# 试用路径(无 token)同样走双密钥窗口
# --------------------------------------------------------------------------- #
def test_trial_path_also_honors_version_routing(tmp_path):
    svc = _svc(tmp_path, v2=NEW_KEY)
    code, body = svc.handle_activate({"device": "dev-trial-new", "client_version": "0.2.7"})
    assert code == 200 and body["mode"] == "trial"
    assert body["master_key"] == NEW_KEY

    code, body = svc.handle_activate({"device": "dev-trial-old"})   # 老客户端试用
    assert code == 200 and body["mode"] == "trial"
    assert body["master_key"] == OLD_KEY


# --------------------------------------------------------------------------- #
# client_version 是【选 key】用的, 不是鉴权用的: 不该影响激活成败/绑定
# --------------------------------------------------------------------------- #
def test_client_version_does_not_affect_auth_outcome(tmp_path):
    """伪造/乱填 client_version 只影响拿哪把 key, 不能绕过 token 校验。"""
    svc = _svc(tmp_path, v2=NEW_KEY)
    code, body = svc.handle_activate(
        {"device": "dev-bad", "token": "not-a-valid-token", "client_version": "9.9.9"}
    )
    assert code in (401, 403)
    assert body["ok"] is False
    assert "master_key" not in body    # 鉴权失败绝不下发任何 key


def test_bad_request_still_rejected_with_version(tmp_path):
    svc = _svc(tmp_path, v2=NEW_KEY)
    code, body = svc.handle_activate({"client_version": "0.2.7"})   # 缺 device
    assert code == 400
    assert "master_key" not in body


# --------------------------------------------------------------------------- #
# ⭐端到端: 客户端真实构造的 payload 喂进服务端(抓字段名不一致这类经典 bug)
# --------------------------------------------------------------------------- #
def _capture_client_payload(monkeypatch, *, version: str) -> dict:
    """跑客户端真实的 _post_activate, 拦住 urlopen 抓它实际发出的 JSON。"""
    from bidking_lab.secure import client_activate as ca

    monkeypatch.setattr(ca, "_client_version", lambda: version)
    seen: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getcode(self):
            return 200

        def read(self):
            return b'{"ok": true}'

    def _fake_urlopen(req, timeout=None):
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    # Activation now owns a no-redirect/proxy-policy opener; keep this payload
    # contract on the established module seam instead of urllib's global opener.
    monkeypatch.setattr(ca, "urlopen_verified", _fake_urlopen)
    ca._post_activate("http://x/activate", "DEV-1", "tok-1", 4.0)
    return seen["payload"]


def test_client_sends_client_version_field(monkeypatch):
    payload = _capture_client_payload(monkeypatch, version="0.2.7")
    assert payload["device"] == "DEV-1"
    assert payload["token"] == "tok-1"
    assert payload["client_version"] == "0.2.7"
    assert payload["client_security_epoch"] == 1


def test_client_omits_version_when_unavailable(monkeypatch):
    """版本取不到时不发这个字段(而不是发空串) -> 服务端按老客户端处理 -> 旧 key。"""
    payload = _capture_client_payload(monkeypatch, version="")
    assert "client_version" not in payload
    assert payload["client_security_epoch"] == 1


def test_end_to_end_client_payload_routes_to_new_key(tmp_path, monkeypatch):
    """客户端(0.2.7)构造的 payload 直接喂服务端 -> 必须拿到新 key。
    若两端字段名对不上(client_version vs clientVersion), 这里立刻红。"""
    payload = _capture_client_payload(monkeypatch, version="0.2.7")
    payload["token"] = _paid_token("u-e2e-new")     # 换成真 token 过鉴权
    svc = _svc(tmp_path, v2=NEW_KEY)
    code, body = svc.handle_activate(payload)
    assert code == 200
    assert body["master_key"] == NEW_KEY


def test_end_to_end_legacy_client_payload_routes_to_old_key(tmp_path, monkeypatch):
    """0.2.6 客户端的 payload(无 client_version)喂进已轮换的服务端 -> 旧 key(旧包不失活)。"""
    payload = _capture_client_payload(monkeypatch, version="")
    payload["token"] = _paid_token("u-e2e-old")
    svc = _svc(tmp_path, v2=NEW_KEY)
    code, body = svc.handle_activate(payload)
    assert code == 200
    assert body["master_key"] == OLD_KEY


# --------------------------------------------------------------------------- #
# 正式切代门：默认兼容，显式 min_epoch=1 后旧包 fail-closed
# --------------------------------------------------------------------------- #
def test_security_epoch_default_zero_preserves_legacy_client(tmp_path):
    svc = _svc(tmp_path, v2=NEW_KEY, min_epoch=0)
    code, body = svc.handle_activate({
        "device": "dev-legacy-default",
        "token": _paid_token("u-legacy-default"),
    })
    assert code == 200
    assert body["master_key"] == OLD_KEY


def test_security_epoch_one_rejects_missing_or_old_client_without_key(tmp_path):
    for index, epoch in enumerate((None, 0)):
        svc = _svc(tmp_path / str(index), v2=NEW_KEY, min_epoch=1)
        payload = {
            "device": f"dev-old-{index}",
            "token": _paid_token(f"u-old-{index}"),
            "client_version": "0.2.8",
        }
        if epoch is not None:
            payload["client_security_epoch"] = epoch
        code, body = svc.handle_activate(payload)
        assert code == 426
        assert body["reason"] == "upgrade_required"
        assert body["min_client_security_epoch"] == 1
        assert "master_key" not in body
        assert "lease" not in body


def test_security_epoch_one_rejects_missing_or_low_client_version(tmp_path):
    for index, version in enumerate((None, "", "0.2.6-hotfix1")):
        svc = _svc(tmp_path / str(index), v2=NEW_KEY, min_epoch=1)
        payload = {
            "device": f"dev-version-{index}",
            "token": _paid_token(f"u-version-{index}"),
            "client_security_epoch": 1,
        }
        if version is not None:
            payload["client_version"] = version
        code, body = svc.handle_activate(payload)
        assert code == 426
        assert body["reason"] == "upgrade_required"
        assert body["min_client_version"] == "0.2.7"
        assert "master_key" not in body
        assert "lease" not in body


def test_security_epoch_one_fails_closed_when_v2_key_is_missing(tmp_path):
    svc = _svc(tmp_path, v2=None, min_epoch=1)
    code, body = svc.handle_activate({
        "device": "dev-current-no-v2",
        "token": _paid_token("u-current-no-v2"),
        "client_version": "0.2.8-hotfix1",
        "client_security_epoch": 1,
    })
    assert code == 503
    assert body == {
        "ok": False,
        "mode": "paid",
        "reason": "security_key_unavailable",
    }


def test_security_epoch_one_accepts_current_paid_and_trial_clients(tmp_path):
    paid = _svc(tmp_path / "paid", v2=NEW_KEY, min_epoch=1)
    code, body = paid.handle_activate({
        "device": "dev-current-paid",
        "token": _paid_token("u-current-paid"),
        "client_version": "0.2.8-hotfix1",
        "client_security_epoch": 1,
    })
    assert code == 200 and body["master_key"] == NEW_KEY

    trial = _svc(tmp_path / "trial", v2=NEW_KEY, min_epoch=1)
    code, body = trial.handle_activate({
        "device": "dev-current-trial",
        "client_version": "0.2.8-hotfix1",
        "client_security_epoch": 1,
    })
    assert code == 200 and body["mode"] == "trial"
    assert body["master_key"] == NEW_KEY


def test_security_epoch_rejects_malformed_values_before_key_selection(tmp_path):
    for index, epoch in enumerate((True, "1", 1.0, -1)):
        svc = _svc(tmp_path / str(index), v2=NEW_KEY, min_epoch=1)
        code, body = svc.handle_activate({
            "device": f"dev-malformed-{index}",
            "client_security_epoch": epoch,
        })
        assert code == 400
        assert body["reason"] == "bad_request"
        assert "master_key" not in body


# --------------------------------------------------------------------------- #
# 独立最低产品版本门：默认关闭；显式启用后在任何状态写入/发 key 前 fail-closed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("minimum", (None, ""))
def test_product_version_gate_default_disabled_preserves_legacy_behavior(
    tmp_path, minimum
):
    """构造器默认 None 与 collector CLI 的空串默认值都只表示未启用版本门。"""
    svc = _svc(tmp_path, v2=NEW_KEY, min_version=minimum)
    code, body = svc.handle_activate({
        "device": "dev-product-default",
        "token": _paid_token("u-product-default"),
    })
    assert code == 200
    assert body["master_key"] == OLD_KEY


@pytest.mark.parametrize(
    "version",
    (
        None,
        "",
        "0.2.8-hotfix1",
        "0.2.9",
        "0.2.9-r1",
        "0.2.9-r99",
        "bad",
        "０.３.１-hotfix２",
        "٠.٣.١-hotfix٢",
        "०.३.१-hotfix२",
        " 0.3.1-hotfix2",
        "0.3.1-hotfix2\t",
        "0.3.1-hotfix2\n",
        "0.3.1-hotfİx2",
        "0.3.1-hotfıx2",
    ),
)
def test_product_version_gate_rejects_old_missing_and_malformed_before_key(
    tmp_path, version
):
    svc = _svc(
        tmp_path,
        v2=NEW_KEY,
        min_version="0.2.9-hotfix1",
    )
    payload = {
        "device": "dev-product-old",
        "token": _paid_token("u-product-old"),
        "client_security_epoch": 1,
    }
    if version is not None:
        payload["client_version"] = version
    code, body = svc.handle_activate(payload)
    assert code == 426
    assert body == {
        "ok": False,
        "mode": "paid",
        "reason": "upgrade_required",
        "min_client_version": "0.2.9-hotfix1",
    }
    assert "master_key" not in body and "lease" not in body
    for filename in ("bindings.json", "paid_starts.json", "trials.json"):
        assert not (tmp_path / filename).exists()


@pytest.mark.parametrize(
    "version",
    (
        "0.2.9-r1",
        " 0.3.1-hotfix2",
        "0.3.1-hotfix2\t",
        "0.3.1-hotfix2\n",
        "0.3.1-hotfİx2",
        "0.3.1-hotfıx2",
        "０.３.１-hotfix２",
        "٠.٣.١-hotfix٢",
        "०.३.१-hotfix२",
    ),
)
def test_product_version_gate_rejects_trial_without_consuming_trial(
    tmp_path, version
):
    svc = _svc(tmp_path, v2=NEW_KEY, min_version="0.2.9-hotfix1")
    code, body = svc.handle_activate({
        "device": "dev-product-trial-old",
        "client_version": version,
        "client_security_epoch": 1,
    })
    assert code == 426
    assert body["reason"] == "upgrade_required"
    assert "master_key" not in body and "lease" not in body
    for filename in ("bindings.json", "paid_starts.json", "trials.json"):
        assert not (tmp_path / filename).exists()


def test_product_version_gate_rejects_protocol_objects_without_calls(tmp_path):
    _ExplosiveVersionStr.calls = 0
    _ExplosiveVersionProtocol.calls = 0
    values = (
        _ExplosiveVersionStr("0.3.1-hotfix2"),
        _ExplosiveVersionProtocol(),
    )
    for index, version in enumerate(values):
        for mode in ("paid", "trial"):
            root = tmp_path / f"{index}-{mode}"
            svc = _svc(root, v2=NEW_KEY, min_version="0.2.9-hotfix1")
            payload = {
                "device": f"dev-product-{index}-{mode}",
                "client_version": version,
                "client_security_epoch": 1,
            }
            if mode == "paid":
                payload["token"] = _paid_token(f"u-product-{index}")
            code, body = svc.handle_activate(payload)
            assert code == 426
            assert body["reason"] == "upgrade_required"
            assert "master_key" not in body and "lease" not in body
            for filename in ("bindings.json", "paid_starts.json", "trials.json"):
                assert not (root / filename).exists()
    assert _ExplosiveVersionStr.calls == 0
    assert _ExplosiveVersionProtocol.calls == 0


def test_product_version_gate_accepts_hotfix_and_newer_paid_and_trial(tmp_path):
    paid = _svc(
        tmp_path / "paid",
        v2=NEW_KEY,
        min_version="0.2.9-hotfix1",
    )
    code, body = paid.handle_activate({
        "device": "dev-product-paid-current",
        "token": _paid_token("u-product-paid-current"),
        "client_version": "0.2.9-hotfix1",
        "client_security_epoch": 1,
    })
    assert code == 200 and body["master_key"] == NEW_KEY

    trial = _svc(
        tmp_path / "trial",
        v2=NEW_KEY,
        min_version="0.2.9-hotfix1",
    )
    code, body = trial.handle_activate({
        "device": "dev-product-trial-newer",
        "client_version": "0.2.10",
        "client_security_epoch": 1,
    })
    assert code == 200 and body["mode"] == "trial"
    assert body["master_key"] == NEW_KEY


@pytest.mark.parametrize(
    "version",
    (
        "0.3.2-hotfix2",
        "0.3.2-hotfix3",
        "0.3.2-hotfix4",
        "0.3.3",
        "0.3.3-hotfix1",
        "0.3.3-hotfix2",
        "0.3.3-hotfix3",
        "0.3.3-hotfix4",
        "0.3.4",
    ),
)
def test_0_3_4_boundary_keeps_supported_clients_eligible(
    tmp_path, version
):
    svc = _svc(
        tmp_path,
        v2=NEW_KEY,
        min_version="0.3.2-hotfix2",
    )
    code, body = svc.handle_activate({
        "device": f"dev-version-boundary-{version}",
        "token": _paid_token(f"u-version-boundary-{version}"),
        "client_version": version,
        "client_security_epoch": 1,
    })
    assert code == 200
    assert body["ok"] is True
    assert body["master_key"] == NEW_KEY


def test_product_version_gate_is_independent_from_v2_key_rollout(tmp_path):
    svc = _svc(
        tmp_path,
        v2=None,
        min_epoch=0,
        min_version="0.2.9-hotfix1",
    )
    code, body = svc.handle_activate({
        "device": "dev-product-no-v2",
        "token": _paid_token("u-product-no-v2"),
        "client_version": "0.2.9-hotfix1",
    })
    assert code == 200
    assert body["master_key"] == OLD_KEY


@pytest.mark.parametrize(
    "minimum",
    (
        "0.2",
        "v0.2.9",
        "0.2.9-preview1",
        " 0.3.1-hotfix2",
        "0.3.1-hotfix2 ",
        "\t",
        "\n",
        "0.3.1-hotfix2\n",
        "0.3.1-hotfİx2",
        "0.3.1-hotfıx2",
    ),
)
def test_product_version_gate_rejects_invalid_server_configuration(tmp_path, minimum):
    with pytest.raises(ValueError, match="invalid min_client_version"):
        _svc(tmp_path, v2=NEW_KEY, min_version=minimum)


def test_server_configuration_rejects_protocol_objects_without_calls(tmp_path):
    _ExplosiveVersionStr.calls = 0
    _ExplosiveVersionProtocol.calls = 0
    for index, minimum in enumerate(
        (_ExplosiveVersionStr("0.3.1-hotfix2"), _ExplosiveVersionProtocol())
    ):
        with pytest.raises(ValueError, match="invalid min_client_version"):
            _svc(tmp_path / str(index), v2=NEW_KEY, min_version=minimum)
    assert _ExplosiveVersionStr.calls == 0
    assert _ExplosiveVersionProtocol.calls == 0


def test_collector_cli_wires_product_gate_independently_and_disabled_by_default():
    collector = (_SCRIPTS / "sample_collector_server.py").read_text(encoding="utf-8-sig")
    assert '"--min-client-version"' in collector
    assert 'default=""' in collector
    assert 'min_client_version=args.min_client_version' in collector
    assert "独立于 --v2-min-version" in collector
