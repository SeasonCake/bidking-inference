"""Synthetic real ActivationService instances; no network or production state."""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import activation_core as core  # noqa: E402
import activation_server as server  # noqa: E402
import membership_card_store as store  # noqa: E402

SECRET = b"synthetic-renewal-service-only-not-production"
BLOCK_KEY = b"synthetic-independent-device-block-key-32"
MASTER = "S0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0s="
NOW = 1_800_000_000
DAY = 86_400


def _service(root: Path, *, enabled=True, pet=True, fault=None):
    root.mkdir(parents=True, exist_ok=True)
    membership = root / "membership.sqlite3"
    if enabled and not membership.exists():
        store.MembershipCardStore.initialize(membership)
    pet_db = root / "pet.sqlite3"
    if pet and not pet_db.exists():
        server.initialize_pet_usage_database(pet_db, secret=SECRET)
    return server.ActivationService(
        secret=SECRET, master_key_b64=MASTER, bindings_path=root / "bindings.json",
        revoked_path=root / "revoked.txt", telemetry_path=root / "telemetry.jsonl",
        trials_path=root / "trials.json", paid_starts_path=root / "paid_starts.json",
        device_block_key=BLOCK_KEY, pet_usage_db_path=pet_db, upload_security_epoch=1,
        membership_store_path=membership if enabled else None, membership_fault_hook=fault,
    )


def _token(uid: str, days=30, *, roaming=False, hours=None):
    duration = None if days is None else (hours * 3600 if hours is not None else days * DAY)
    return core.mint_token(uid, SECRET, iat=1_600_000_000,
                           exp=None if duration is None else 1_600_000_000 + duration, roaming=roaming)


def _request(service, token=None, card=None, *, device="fixture-device", event=None):
    body = {"device": device, "client_version": "0.3.6", "client_security_epoch": 1, "upload_lease_schema": 1}
    if token is not None:
        body["token"] = token
    if card is not None:
        body.update(card_token=card, card_redemption_schema=1)
    if event is not None:
        body["pet_usage"] = event
    return service.handle_activate(body)


def _lease(body):
    return json.loads(body["lease"].rsplit(".", 1)[0])


def _legacy(service, uid="fixture-A", days=30, *, left=15, record="fixture-device"):
    server._atomic_write_json(service.bindings_path, {uid: record})
    server._atomic_write_json(service.paid_starts_path, {uid: NOW - (days - left) * DAY})


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(server.time, "time", lambda: NOW)


@pytest.mark.parametrize("old,new,left,plan,full,private", [
    (30, 90, 105, "90d", 105, None), (90, 30, 45, "90d", 15, None),
    (365, 90, 105, "365d", 105, 15), (365, 365, 380, "365d", 380, 380),
])
def test_actual_service_tier_snapshot_and_stable_ack(tmp_path, old, new, left, plan, full, private):
    service = _service(tmp_path)
    _legacy(service, days=old)
    a, b = _token("fixture-A", old), _token("fixture-B", new)
    code, body = _request(service, a, b)
    assert code == 200 and body["ok"] is True
    lease = _lease(body)
    assert lease["uid"] == "fixture-A" and lease["paid_exp"] == NOW + left * DAY
    assert lease["plan_kind"] == plan
    assert lease["membership"]["my_records_exp"] == NOW + full * DAY
    assert lease["membership"]["private_dashboard_exp"] == (None if private is None else NOW + private * DAY)
    assert body["card_redemption"]["status"] == "redeemed"
    replay_code, replay = _request(_service(tmp_path), a, b)
    assert replay_code == 200 and replay["card_redemption"]["status"] == "replayed"
    assert replay["card_redemption"]["receipt_id"] == body["card_redemption"]["receipt_id"]
    assert _lease(replay)["paid_exp"] == lease["paid_exp"]
    alias_code, alias = _request(service, b)
    assert alias_code == 200 and _lease(alias)["uid"] == "fixture-A"
    assert "fixture-B" not in json.loads(service.bindings_path.read_text())


@pytest.mark.parametrize("days,hours", [(30, None), (None, None), (30, 3), (7, None)])
def test_unconfigured_and_configured_ordinary_v1_compatibility(tmp_path, days, hours):
    for enabled in (False, True):
        service = _service(tmp_path / str(enabled), enabled=enabled)
        code, body = _request(service, _token("fixture-A", days, hours=hours))
        assert code == 200 and body["mode"] == "paid"
        lease = _lease(body)
        assert lease["uid"] == "fixture-A"
        expected = None if days is None else NOW + (hours * 3600 if hours is not None else days * DAY)
        assert lease.get("paid_exp") == expected
        assert "card_redemption" not in body
        if not enabled:
            assert "membership" not in lease and not (service.bindings_path.parent / "membership.sqlite3").exists()


def test_mixed_used_trial_expired_actions_do_not_damage_current(tmp_path):
    service = _service(tmp_path)
    _legacy(service)
    a = _token("fixture-A")
    baseline = (service.bindings_path.read_bytes(), service.paid_starts_path.read_bytes())
    code, body = _request(service, a, _token("fixture-B", 90, roaming=True))
    assert code == 409 and body["card_redemption"]["status"] == "switch_required_permissions"
    assert baseline == (service.bindings_path.read_bytes(), service.paid_starts_path.read_bytes())
    assert _request(service, a)[0] == 200
    # The mixed card remains valid for the separate ordinary switch path.
    assert _request(service, _token("fixture-B", 90, roaming=True))[0] == 200
    code, body = _request(service, None, _token("fixture-C"))
    assert code == 409 and body["card_redemption"]["status"] == "unsupported_current_membership"
    assert not service.trials_path.exists()
    code, _ = _request(service, _token("fixture-Z"), _token("fixture-D"))
    assert code == 409 and "fixture-Z" not in json.loads(service.paid_starts_path.read_text())


def test_unconfigured_card_request_has_no_false_success_ack(tmp_path):
    service = _service(tmp_path, enabled=False)
    _legacy(service)
    code, body = _request(service, _token("fixture-A"), _token("fixture-B"))
    assert code == 409 and body["card_redemption"]["status"] == "renewal_not_configured"
    assert "lease" not in body


def test_same_roaming_card_lazily_adopts_v6_and_keeps_existing_counter(tmp_path):
    for versioned in (False, True):
        service = _service(tmp_path / str(versioned))
        binding = {"schema_version": 1, "device": "fixture-device", "roaming_rebinds_used": 5} if versioned else "fixture-device"
        _legacy(service, record=binding)
        before = service.bindings_path.read_bytes()
        code, body = _request(service, _token("fixture-A", roaming=True), _token("fixture-B", 90, roaming=True))
        assert code == 200 and body["card_redemption"]["roaming_rebinds_added"] == 0
        if versioned:
            assert service.bindings_path.read_bytes() == before
        else:
            assert json.loads(service.bindings_path.read_text())["fixture-A"] == {
                "schema_version": 1, "device": "fixture-device", "roaming_rebinds_used": 0}
        lease = _lease(body)
        if versioned:
            assert lease["roaming_rebinds"]["used"] == 5
        else:
            assert lease["roaming_rebinds"]["used"] == 0 and lease["roaming_rebinds"]["remaining"] == 8


def test_roaming_alias_shares_eight_counter_current_machine_remains_valid(tmp_path):
    service = _service(tmp_path)
    _legacy(service)
    a, b = _token("fixture-A", roaming=True), _token("fixture-B", 90, roaming=True)
    assert _request(service, a, b)[0] == 200
    for used in range(1, 9):
        code, body = _request(service, b, device=f"fixture-machine-{used}")
        assert code == 200 and _lease(body)["roaming_rebinds"]["used"] == used
        assert _lease(body)["membership"]["credential_uid"] == "fixture-B"
        assert _lease(body)["membership"]["canonical_uid"] == "fixture-A"
    before = service.bindings_path.read_bytes()
    code, body = _request(service, a, device="fixture-machine-9")
    assert code == 403 and body["reason"] == "device_mismatch" and body["detail"] == "roaming_rebind_limit"
    assert body["roaming_rebinds"]["used"] == 8
    code, body = _request(service, a, device="fixture-machine-8")
    assert code == 200 and _lease(body)["roaming_rebinds"]["remaining"] == 0
    code, body = _request(service, a, _token("fixture-C", 30, roaming=True), device="fixture-machine-8")
    assert code == 200 and _lease(body)["roaming_rebinds"]["used"] == 8
    assert service.bindings_path.read_bytes() == before


def test_renewal_other_roaming_device_does_not_consume_new_card(tmp_path):
    service = _service(tmp_path)
    _legacy(service)
    a, b = _token("fixture-A", roaming=True), _token("fixture-B", 90, roaming=True)
    before = (service.bindings_path.read_bytes(), service.paid_starts_path.read_bytes())
    code, body = _request(service, a, b, device="fixture-other-machine")
    assert code == 409 and body["card_redemption"]["status"] == "switch_required_device_activation"
    assert service.membership_store.read("fixture-A", now=NOW) is None
    assert before == (service.bindings_path.read_bytes(), service.paid_starts_path.read_bytes())


def test_anchor_and_source_revoke_all_alias_paths(tmp_path):
    service = _service(tmp_path)
    _legacy(service)
    a, b = _token("fixture-A"), _token("fixture-B", 365)
    code, body = _request(service, a, b)
    assert code == 200
    upload = body["upload_lease"]
    assert service.authorize_upload_lease(upload, now=NOW)[0] is True
    assert service.authorize_my_records(a, now=NOW)[2]["uid"] == "fixture-A"
    assert service.authorize_private_dashboard(b, now=NOW)[2]["uid"] == "fixture-A"
    for revoked_uid in ("fixture-B", "fixture-A"):
        service.revoked_path.write_text(revoked_uid + "\n", encoding="utf-8")
        assert _request(service, a)[0] == 403
        assert _request(service, b)[0] == 403
        assert service.authorize_my_records(a, now=NOW)[:2] == (False, "revoked")
        assert service.authorize_private_dashboard(b, now=NOW)[:2] == (False, "revoked")
        assert service.authorize_upload_lease(upload, now=NOW)[:2] == (False, "identity_revoked")


def test_feature_auth_deadline_not_membership_deadline(tmp_path):
    service = _service(tmp_path)
    _legacy(service, days=365)
    a, b = _token("fixture-A", 365), _token("fixture-B", 90)
    assert _request(service, a, b)[0] == 200
    private = service.authorize_private_dashboard(a, now=NOW)[2]
    records = service.authorize_my_records(b, now=NOW)[2]
    assert private["feature_exp"] == NOW + 15 * DAY and private["membership_exp"] == NOW + 105 * DAY
    assert records["feature_exp"] == NOW + 105 * DAY
    assert service.authorize_private_dashboard(a, now=NOW + 15 * DAY)[:2] == (False, "plan_required")
    assert service.authorize_my_records(a, now=NOW + 15 * DAY)[0] is True


def test_browser_first_use_claim_blocks_card_redemption(tmp_path):
    first, second = _service(tmp_path), _service(tmp_path)
    _legacy(first)
    a, b = _token("fixture-A"), _token("fixture-B", 365)
    assert second.authorize_private_dashboard(b, now=NOW)[0] is True
    code, body = _request(first, a, b)
    assert code == 409 and body["card_redemption"]["status"] == "switch_required_used_card"
    assert "fixture-B" not in json.loads(first.bindings_path.read_text())
    assert _request(second, b)[0] == 200


@pytest.mark.parametrize("phase,committed", [
    ("before_pending_projection", False), ("after_pending_projection", False),
    ("before_membership_claim", False), ("after_membership_claim", True),
    ("before_paid_start_projection", True), ("after_paid_start_projection", True),
    ("before_binding_projection", True), ("after_binding_projection", True),
])
def test_first_use_recovery_before_after_sql_and_json(tmp_path, monkeypatch, phase, committed):
    def fail(seen):
        if seen == phase:
            raise RuntimeError("synthetic projection failure")
    service = _service(tmp_path, fault=fail)
    a = _token("fixture-A")
    code, body = _request(service, a)
    assert code == 503 and "lease" not in body and "master_key" not in body
    if not committed:
        assert service.membership_store.read("fixture-A", now=NOW) is None
    monkeypatch.setattr(server.time, "time", lambda: NOW + 3600)
    fresh = _service(tmp_path)
    code, body = _request(fresh, a)
    expected_start = NOW if committed else NOW + 3600
    assert code == 200 and _lease(body)["paid_exp"] == expected_start + 30 * DAY
    assert fresh.membership_store.read("fixture-A", now=NOW).revision == 1
    assert len(fresh.membership_store.claims_for_account("fixture-A")) == 1
    assert json.loads(fresh.paid_starts_path.read_text())["fixture-A"] == expected_start
    assert fresh._membership_pending()["entries"] == {}


def test_renewal_commit_confirmation_recovery_without_second_credit(tmp_path):
    def fail(phase):
        if phase == "after_membership_redeem":
            raise RuntimeError("synthetic lost confirmation")
    service = _service(tmp_path, fault=fail)
    _legacy(service)
    a, b = _token("fixture-A"), _token("fixture-B")
    before = (service.bindings_path.read_bytes(), service.paid_starts_path.read_bytes())
    code, body = _request(service, a, b)
    assert code == 503 and "lease" not in body
    assert before == (service.bindings_path.read_bytes(), service.paid_starts_path.read_bytes())
    code, body = _request(_service(tmp_path), a, b)
    assert code == 200 and body["card_redemption"]["status"] == "replayed"
    snap = _service(tmp_path).membership_store.read("fixture-A", now=NOW)
    assert snap.membership_exp == NOW + 45 * DAY and snap.pet_purchased_quota_seconds == 86_400
    assert len(snap.purchase_grants) == 1


def test_pet_failure_does_not_block_core_renewal(tmp_path):
    service = _service(tmp_path, pet=False)
    _legacy(service)
    code, body = _request(service, _token("fixture-A"), _token("fixture-B"))
    assert code == 200 and _lease(body)["paid_exp"] == NOW + 45 * DAY
    assert _lease(body)["feature_entitlements"]["pet_companion"] == "none"
    assert _lease(body)["feature_usage"]["pet_companion"]["status"] == "state_error"
    assert service.membership_store.read("fixture-A", now=NOW).pet_preserved_used_seconds is None


def test_quota_grant_waits_for_live_authority_without_seed_balance_reset(tmp_path):
    service = _service(tmp_path)
    _legacy(service)
    a = _token("fixture-A")
    assert _request(service, a, event={"session_id": "synthetic-bank-session-0001", "seq": 0, "visible": True})[0] == 200
    original = service._pet_usage_transaction
    service._pet_usage_transaction = lambda **_: (_ for _ in ()).throw(server.StateFileError("synthetic authority unavailable"))
    code, body = _request(service, a, _token("fixture-B"))
    assert code == 200 and _lease(body)["feature_usage"]["pet_companion"]["kind"] == "none"
    service._pet_usage_transaction = original
    code, body = _request(_service(tmp_path), a, _token("fixture-B"))
    assert code == 200 and body["card_redemption"]["status"] == "replayed"
    pet = _lease(body)["feature_usage"]["pet_companion"]
    assert pet["total_seconds"] == 86_400 and pet["last_seq"] == 0


def test_full_period_boundary_keeps_core_then_switches_to_quota(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _legacy(service, days=90)
    a = _token("fixture-A", 90)
    assert _request(service, a, _token("fixture-B"))[0] == 200
    monkeypatch.setattr(server.time, "time", lambda: NOW + 15 * DAY)
    code, body = _request(service, a)
    assert code == 200 and _lease(body)["paid_exp"] == NOW + 45 * DAY
    pet = _lease(body)["feature_usage"]["pet_companion"]
    assert pet["kind"] == "quota" and pet["remaining_seconds"] == 43_200


def test_malformed_card_request_and_corrupt_member_db_fail_without_grant(tmp_path):
    service = _service(tmp_path)
    _legacy(service)
    a = _token("fixture-A")
    code, body = service.handle_activate({"device": "fixture-device", "token": a,
                                         "card_token": _token("fixture-B"), "card_redemption_schema": True})
    assert code == 400 and "lease" not in body
    assert _request(service, a)[0] == 200
    with __import__("sqlite3").connect(service.membership_store.path) as connection:
        connection.execute("PRAGMA user_version=2")
    code, body = _request(service, a)
    assert code == 503 and "lease" not in body
    assert service.authorize_my_records(a, now=NOW)[:2] == (False, "state_error")


def test_live_quota_used_and_sequence_carry_once(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _legacy(service)
    a = _token("fixture-A")
    event = {"session_id": "synthetic-visible-session-0001", "seq": 0, "visible": True}
    code, body = _request(service, a, event=event)
    assert code == 200
    first_pet = _lease(body)["feature_usage"]["pet_companion"]
    assert first_pet["remaining_seconds"] == 43_200
    monkeypatch.setattr(server.time, "time", lambda: NOW + 10)
    code, body = _request(service, a, _token("fixture-B"))
    pet = _lease(body)["feature_usage"]["pet_companion"]
    assert code == 200 and pet["total_seconds"] == 86_400
    assert pet["remaining_seconds"] == 86_390 and pet["last_seq"] == 0
    revision = pet["usage_revision"]
    code, body = _request(_service(tmp_path), a, _token("fixture-B"))
    pet2 = _lease(body)["feature_usage"]["pet_companion"]
    assert pet2["remaining_seconds"] == 86_390 and pet2["usage_revision"] == revision


def test_two_service_instances_first_use_and_redemption_share_gate(tmp_path):
    first, second = _service(tmp_path), _service(tmp_path)
    _legacy(first)
    a, b = _token("fixture-A"), _token("fixture-B", 90)
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = [workers.submit(_request, first, a, b), workers.submit(_request, second, b)]
        values = [future.result(timeout=10) for future in outcomes]
    claim = first.membership_store.lookup_claim(store.VerifiedCard("fixture-B",
        hashlib.sha256(b.rsplit(".", 1)[0].encode()).hexdigest(), 90 * DAY, "bound", "unused"))
    assert claim is not None
    if claim.operation == "redeem":
        assert values[0][0] == values[1][0] == 200
        assert _lease(values[1][1])["uid"] == "fixture-A"
        assert "fixture-B" not in json.loads(first.bindings_path.read_text())
    else:
        assert values[0][0] == 409 and values[1][0] == 200
        assert _lease(values[1][1])["uid"] == "fixture-B"


def test_two_services_different_cards_same_account_no_lost_updates(tmp_path):
    first, second = _service(tmp_path), _service(tmp_path)
    _legacy(first)
    a = _token("fixture-A")
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(_request, first, a, _token("fixture-B")),
                   workers.submit(_request, second, a, _token("fixture-C", 90))]
        results = [future.result(timeout=10) for future in futures]
    assert all(code == 200 for code, _ in results)
    snapshot = first.membership_store.read("fixture-A", now=NOW)
    assert snapshot.membership_exp == NOW + 135 * DAY and snapshot.revision == 3
    assert snapshot.pet_purchased_quota_seconds == 86_400


def test_two_services_two_stock_first_activations_keep_both_json_rows(tmp_path):
    first, second = _service(tmp_path), _service(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(_request, first, _token("fixture-A")),
                   workers.submit(_request, second, _token("fixture-B"), device="fixture-device-B")]
        assert all(future.result(timeout=10)[0] == 200 for future in futures)
    assert set(json.loads(first.bindings_path.read_text())) == {"fixture-A", "fixture-B"}
    assert set(json.loads(first.paid_starts_path.read_text())) == {"fixture-A", "fixture-B"}


def test_bounded_existing_legacy_start_skew_stays_ordinary_compatible(tmp_path):
    service = _service(tmp_path)
    _legacy(service, days=365)
    server._atomic_write_json(service.paid_starts_path, {"fixture-A": NOW + 60})
    a = _token("fixture-A", 365)
    code, body = _request(service, a)
    assert code == 200 and _lease(body)["paid_exp"] == NOW + 60 + 365 * DAY
    assert service.authorize_private_dashboard(a, now=NOW)[0] is True
    before = service.paid_starts_path.read_bytes()
    code, body = _request(service, a, _token("fixture-B"))
    assert code == 409 and body["card_redemption"]["status"] == "unsupported_current_membership"
    assert service.paid_starts_path.read_bytes() == before


@pytest.mark.parametrize("phase", ["before_binding_projection", "after_binding_projection"])
def test_renewal_v5_counter_takeover_projection_failure_recovers_once(tmp_path, phase):
    def fail(seen):
        if seen == phase:
            raise RuntimeError("synthetic renewal projection failure")
    service = _service(tmp_path, fault=fail)
    _legacy(service)
    a, b = _token("fixture-A", roaming=True), _token("fixture-B", 90, roaming=True)
    code, body = _request(service, a, b)
    assert code == 503 and body["card_redemption"]["committed"] is None
    assert "lease" not in body and "master_key" not in body
    fresh = _service(tmp_path)
    code, body = _request(fresh, a, b)
    assert code == 200 and body["card_redemption"]["status"] == "replayed"
    assert _lease(body)["paid_exp"] == NOW + 105 * DAY
    assert _lease(body)["roaming_rebinds"]["used"] == 0
    snapshot = fresh.membership_store.read("fixture-A", now=NOW)
    assert snapshot.revision == 2 and len(snapshot.purchase_grants) == 1
    assert fresh._membership_pending()["entries"] == {}


@pytest.mark.parametrize("enabled", [False, True])
def test_revoked_canonical_uid_hash_projection_preserves_legacy_and_alias_cookie(tmp_path, enabled):
    service = _service(tmp_path, enabled=enabled)
    _legacy(service)
    if enabled:
        assert _request(service, _token("fixture-A"), _token("fixture-B", 90))[0] == 200
    h = lambda uid: hashlib.sha256(uid.encode()).hexdigest()[:16]
    assert service.revoked_canonical_uid_hashes() == frozenset()
    before = (service.bindings_path.read_bytes(), service.paid_starts_path.read_bytes())
    service.revoked_path.write_text("fixture-B\nfixture-unclaimed\n", encoding="utf-8")
    hashes = service.revoked_canonical_uid_hashes()
    expected = {h("fixture-B"), h("fixture-unclaimed")}
    if enabled:
        expected.add(h("fixture-A"))
    assert hashes == frozenset(expected)
    assert h("fixture-unrelated-member") not in hashes
    assert before == (service.bindings_path.read_bytes(), service.paid_starts_path.read_bytes())


@pytest.mark.parametrize("damage", ["claimed-lineage", "empty-revoked-schema"])
def test_revoked_canonical_projection_corrupt_store_is_unknown_not_empty_allow(tmp_path, damage):
    service = _service(tmp_path)
    _legacy(service)
    assert _request(service, _token("fixture-A"), _token("fixture-B", 90))[0] == 200
    with __import__("sqlite3").connect(service.membership_store.path) as connection:
        if damage == "claimed-lineage":
            service.revoked_path.write_text("fixture-B\n", encoding="utf-8")
            connection.execute("DELETE FROM card_claims WHERE card_uid=?", ("fixture-A",))
        else:
            connection.execute("PRAGMA user_version=2")
    assert service.revoked_canonical_uid_hashes() is None


def _process_worker(root, ready, start, output, mode):
    """Real separate service process; only synthetic UID/status summaries leave it."""
    server.time.time = lambda: NOW
    service = _service(Path(root), pet=False)
    Path(ready).touch()
    deadline = time.monotonic() + 8
    while not Path(start).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("synthetic process start deadline")
        time.sleep(0.01)
    if mode == "activate-B":
        code, body = _request(service, _token("fixture-B", 90))
    elif mode == "redeem-B":
        code, body = _request(service, _token("fixture-A"), _token("fixture-B", 90))
    elif mode == "redeem-C":
        code, body = _request(service, _token("fixture-A"), _token("fixture-C"))
    else:
        raise RuntimeError("synthetic worker mode")
    lease = _lease(body) if code == 200 else {}
    Path(output).write_text(json.dumps({"code": code, "uid": lease.get("uid"),
        "paid_exp": lease.get("paid_exp"), "status": body.get("card_redemption", {}).get("status")}), encoding="utf-8")


@pytest.mark.parametrize("second_mode", ["activate-B", "redeem-C"])
def test_two_process_services_first_use_renew_and_distinct_cards_share_gate(tmp_path, second_mode):
    service = _service(tmp_path, pet=False)
    _legacy(service)
    start = tmp_path / "race-start"
    processes = []
    try:
        for index, mode in enumerate(("redeem-B", second_mode)):
            ready, output = tmp_path / f"ready-{index}", tmp_path / f"result-{index}.json"
            process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                "--membership-worker", str(tmp_path), str(ready), str(start), str(output), mode],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append((process, ready, output))
        deadline = time.monotonic() + 10
        while not all(ready.exists() for _, ready, _ in processes):
            assert all(process.poll() is None for process, _, _ in processes), "synthetic worker exited before readiness"
            assert time.monotonic() < deadline, "synthetic workers exceeded readiness deadline"
            time.sleep(0.01)
        start.touch()
        results = []
        for process, _, output in processes:
            stdout, stderr = process.communicate(timeout=12)
            assert process.returncode == 0, f"synthetic worker failed: {stdout} {stderr}"
            results.append(json.loads(output.read_text(encoding="utf-8")))
        snapshot = service.membership_store.read("fixture-A", now=NOW)
        if second_mode == "redeem-C":
            assert [result["code"] for result in results] == [200, 200]
            assert snapshot.revision == 3 and snapshot.membership_exp == NOW + 135 * DAY
            assert len(snapshot.purchase_grants) == 2
        elif results[0]["code"] == 200:
            assert results[1]["code"] == 200 and results[1]["uid"] == "fixture-A"
            assert snapshot.membership_exp == NOW + 105 * DAY
            assert "fixture-B" not in json.loads(service.bindings_path.read_text())
        else:
            assert results[0]["code"] == 409 and results[0]["status"] == "switch_required_used_card"
            assert results[1]["code"] == 200 and results[1]["uid"] == "fixture-B"
            assert service.membership_store.read("fixture-B", now=NOW).revision == 1
        assert service._membership_pending()["entries"] == {}
    finally:
        for process, _, _ in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_configured_busy_trial_gate_returns_structured_failure_without_trial_write(tmp_path, monkeypatch):
    service = _service(tmp_path)
    def unavailable():
        raise server.StateFileError("synthetic cooperative gate deadline")
    monkeypatch.setattr(service, "_membership_request_gate", unavailable)
    assert _request(service) == (503, {"ok": False, "mode": "trial", "reason": "state_error"})
    assert not service.trials_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows byte-range lock behavior")
@pytest.mark.parametrize("initial", [b"", b"\x7f"])
def test_cold_gate_waits_without_writing_through_an_existing_lock(tmp_path, initial):
    import msvcrt
    service = _service(tmp_path, pet=False)
    _legacy(service)
    path = service._membership_gate_path
    path.write_bytes(initial)
    owner = os.open(path, os.O_RDWR | os.O_BINARY)
    msvcrt.locking(owner, msvcrt.LK_NBLCK, 1)
    entered = threading.Event()
    def request():
        entered.set()
        return _request(service, _token("fixture-A"), _token("fixture-B"))
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(request)
        try:
            assert entered.wait(2)
            time.sleep(.08)
            assert not pending.done(), "held gate must wait, not fail at pre-lock initialization"
        finally:
            os.lseek(owner, 0, os.SEEK_SET)
            msvcrt.locking(owner, msvcrt.LK_UNLCK, 1)
            os.close(owner)
        code, body = pending.result(timeout=5)
    assert code == 200 and body["card_redemption"]["status"] == "redeemed"
    assert service.membership_store.read("fixture-A", now=NOW).revision == 2
    assert path.read_bytes() == initial


def test_gate_releases_owned_descriptor_when_stream_construction_fails(tmp_path, monkeypatch):
    service = _service(tmp_path, pet=False)
    closed = []
    original_close = os.close
    def close(fd):
        closed.append(fd)
        return original_close(fd)
    def unavailable(*_args, **_kwargs):
        raise OSError("synthetic fdopen failure")
    monkeypatch.setattr(server.os, "fdopen", unavailable)
    monkeypatch.setattr(server.os, "close", close)
    with pytest.raises(server.StateFileError, match="membership gate unavailable"):
        with service._membership_request_gate():
            pytest.fail("unavailable gate admitted work")
    assert len(closed) == 1
    with pytest.raises(OSError):
        os.fstat(closed[0])


if __name__ == "__main__":
    if len(sys.argv) == 7 and sys.argv[1] == "--membership-worker":
        try:
            _process_worker(*sys.argv[2:])
        except Exception as error:
            print(type(error).__name__ + ": synthetic worker failed", file=sys.stderr)
            raise SystemExit(2) from None
    else:
        raise SystemExit("synthetic worker arguments required")
