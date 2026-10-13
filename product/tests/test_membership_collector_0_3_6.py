"""No-network collector integration for purchased feature deadlines and cards."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_membership_card_service_0_3_6 import DAY, NOW, _legacy, _request, _service, _token, server
import my_records
import sample_collector_server as collector


@pytest.mark.parametrize("days,method,cookie_maker,cookie_reader", [
    (90, "_my_uid_hash_from_token", "_my_make_cookie", "_my_read_cookie"),
    (365, "_dashboard_uid_hash_from_token", "_dash_make_cookie", "_dash_read_cookie"),
])
def test_renewal_alias_keeps_canonical_cookie_but_not_later_base_expiry(tmp_path, monkeypatch, days, method, cookie_maker, cookie_reader):
    monkeypatch.setattr(server.time, "time", lambda: NOW)
    service = _service(tmp_path)
    _legacy(service, days=days)
    a, b = _token("fixture-A", days), _token("fixture-B", 30)
    assert _request(service, a, b)[0] == 200
    monkeypatch.setattr(collector, "_ACT", service)
    at = NOW + 14 * DAY
    monkeypatch.setattr(server.time, "time", lambda: at)
    result = getattr(collector, method)(a, now=at)
    assert result == getattr(collector, method)(b, now=at)
    uid_hash, error, expiry = result
    assert not error and uid_hash == my_records.uid_hash_of("fixture-A")
    assert expiry == NOW + 15 * DAY
    made = getattr(collector, cookie_maker)(uid_hash, expiry, now=at)
    cookie = made[0] if isinstance(made, tuple) else made
    assert int(cookie.split("|")[1]) == expiry
    assert getattr(collector, cookie_reader)(cookie, now=expiry) is None
    assert getattr(collector, method)(a, now=expiry)[0] is None


@pytest.mark.parametrize("bad", [None, True, "100", 101, -1])
def test_renewal_aware_malformed_feature_deadline_never_falls_back(bad):
    with pytest.raises(ValueError):
        collector._feature_cookie_expiry({"membership_revision": 2, "membership_exp": 100, "feature_exp": bad})
    assert collector._feature_cookie_expiry({"membership_exp": None}) is None
    assert collector._feature_cookie_expiry({"membership_exp": 100}) == 100


def test_collector_passes_card_request_and_ack_without_replacing_token(monkeypatch):
    body = {"token": "synthetic-current-token", "card_token": "synthetic-new-card", "card_redemption_schema": 1}
    reply = {"ok": False, "mode": "paid", "reason": "card_action", "card_redemption": {"status": "switch_required_permissions", "committed": False}}
    captured = []
    def handle(value):
        captured.append(dict(value))
        return 409, reply
    monkeypatch.setattr(collector, "_ACT", SimpleNamespace(handle_activate=handle))
    assert collector._handle_activation_body(body) == (409, reply)
    assert captured == [body] and captured[0]["token"] == "synthetic-current-token"


def test_canonical_cookie_revocation_failure_is_closed_and_nonmatching_is_valid(monkeypatch):
    uid_hash = my_records.uid_hash_of("fixture-A")
    for hashes, revoked in ((frozenset({uid_hash}), True), (frozenset(), False), (None, True), ({uid_hash}, True)):
        monkeypatch.setattr(collector, "_ACT", SimpleNamespace(revoked_canonical_uid_hashes=lambda: hashes))
        assert collector._uid_hash_is_revoked(uid_hash) is revoked


def test_actual_redeemed_source_revocation_closes_both_canonical_cookies(tmp_path, monkeypatch):
    monkeypatch.setattr(server.time, "time", lambda: NOW)
    service = _service(tmp_path)
    _legacy(service, days=365)
    a, b = _token("fixture-A", 365), _token("fixture-B", 90)
    assert _request(service, a, b)[0] == 200
    monkeypatch.setattr(collector, "_ACT", service)
    uid_hash = my_records.uid_hash_of("fixture-A")
    my_cookie = collector._my_make_cookie(uid_hash, NOW + DAY, now=NOW)
    dash_cookie, _ = collector._dash_make_cookie(uid_hash, NOW + DAY, now=NOW)
    assert collector._my_read_cookie(my_cookie, now=NOW) == uid_hash
    assert collector._dash_read_cookie(dash_cookie, now=NOW) == uid_hash
    service.revoked_path.write_text("fixture-B\n", encoding="utf-8")
    assert collector._my_read_cookie(my_cookie, now=NOW) is None
    assert collector._dash_read_cookie(dash_cookie, now=NOW) is None
    assert not collector._uid_hash_is_revoked(my_records.uid_hash_of("unrelated-fixture"))
