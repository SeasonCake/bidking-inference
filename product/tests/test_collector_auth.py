# -*- coding: utf-8 -*-
"""collector /ingest 认证: HMAC 签名(推荐, key 不过网+防重放) + X-Api-Key(legacy 回退)。"""
from __future__ import annotations
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sample_collector_server as cs  # noqa: E402


def _sign(key: str, ts: str, body: bytes) -> str:
    # 必须与【客户端 sample_upload._post】+【服务端 _auth_ok】完全同式, 否则验签不过。
    return hmac.new(key.encode("utf-8"), ts.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()


def _h(**kw):
    return kw  # _auth_ok 只用 headers.get(name), dict 即可


def test_hmac_valid_accepts(monkeypatch):
    monkeypatch.setattr(cs, "_KEY", "k_secret")
    body = b'{"map_id":2402}'
    ts = str(int(time.time()))
    assert cs._auth_ok(_h(**{"X-Sig-Ts": ts, "X-Sig": _sign("k_secret", ts, body)}), body) is True


def test_hmac_wrong_sig_or_tampered_body_rejects(monkeypatch):
    monkeypatch.setattr(cs, "_KEY", "k_secret")
    body = b'{"map_id":2402}'
    ts = str(int(time.time()))
    good = _sign("k_secret", ts, body)
    assert cs._auth_ok(_h(**{"X-Sig-Ts": ts, "X-Sig": good[:-1] + ("0" if good[-1] != "0" else "1")}), body) is False
    # 篡改 body -> 同签名验不过(防篡改)
    assert cs._auth_ok(_h(**{"X-Sig-Ts": ts, "X-Sig": good}), b'{"map_id":9999}') is False
    # 错 key 签的 -> 拒
    assert cs._auth_ok(_h(**{"X-Sig-Ts": ts, "X-Sig": _sign("wrong", ts, body)}), body) is False


def test_hmac_expired_timestamp_rejects_replay(monkeypatch):
    monkeypatch.setattr(cs, "_KEY", "k_secret")
    body = b'{"map_id":2402}'
    old = str(int(time.time()) - int(cs._SIG_WINDOW) - 60)   # 超窗口 = 重放
    assert cs._auth_ok(_h(**{"X-Sig-Ts": old, "X-Sig": _sign("k_secret", old, body)}), body) is False
    assert cs._auth_ok(_h(**{"X-Sig-Ts": "not-a-number", "X-Sig": "x"}), body) is False


def test_legacy_api_key_still_accepted(monkeypatch):
    monkeypatch.setattr(cs, "_KEY", "k_secret")
    assert cs._auth_ok(_h(**{"X-Api-Key": "k_secret"}), b"{}") is True
    assert cs._auth_ok(_h(**{"X-Api-Key": "wrong"}), b"{}") is False
    assert cs._auth_ok(_h(), b"{}") is False                  # 既无签名也无 key -> 拒


def test_no_key_configured_is_open(monkeypatch):
    monkeypatch.setattr(cs, "_KEY", "")
    assert cs._auth_ok(_h(), b"{}") is True                    # 本地/测试: 没配 key = 开放


def test_client_signature_roundtrips_with_server(monkeypatch):
    """客户端 sample_upload 的签名公式 == 服务端 _auth_ok 校验(契约锁: 任一侧改公式即挂)。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from bidking_lab.live import sample_upload as su  # noqa: E402
    monkeypatch.setattr(cs, "_KEY", "k_secret")
    captured = {}
    statuses = []

    class _FakeResp:
        def getcode(self): return 200
        def read(self):
            return json.dumps({"accepted": 1, "duplicate": 0, "quarantined": 0, "rejected": 0}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=0):
        captured["ts"] = req.get_header("X-sig-ts")
        captured["sig"] = req.get_header("X-sig")
        captured["body"] = req.data
        captured["has_key_header"] = req.get_header("X-api-key")  # 应为 None(key 不过网)
        return _FakeResp()

    monkeypatch.setattr(su.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(su, "_write_upload_status", statuses.append)
    su._post("http://srv/ingest", "k_secret", {"map_id": 2402, "hero": "aisha"})
    assert captured["has_key_header"] is None                   # 关键: 明文 key 不再过网
    assert cs._auth_ok(_h(**{"X-Sig-Ts": captured["ts"], "X-Sig": captured["sig"]}), captured["body"]) is True
    assert "OK 200 accepted=1 duplicate=0 quarantined=0 rejected=0" in statuses[-1]


def test_client_upload_status_marks_unreported_legacy_or_invalid_response():
    """旧 collector 空响应或代理返回非 JSON 时仍算 HTTP 成功，但不能伪装成已进入主库。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from bidking_lab.live import sample_upload as su  # noqa: E402

    assert su._upload_result_summary(b"") == "result=unreported"
    assert su._upload_result_summary(b"not-json") == "result=unreported"
    assert su._upload_result_summary(b'[{"accepted": 1}]') == "result=unreported"
    assert su._upload_result_summary(b'{"accepted": true}') == "result=unreported"
