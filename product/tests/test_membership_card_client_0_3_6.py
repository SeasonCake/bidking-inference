"""Synthetic wire/parser/transport controls: no real credentials or network."""
import base64
import json
import threading

import pytest
from bidking_lab.secure import client_activate as client
from bidking_lab.secure import enforce, activation
from test_activation_candidate_atomic_commit_0_3_1 import isolated_activation_runtime

NOW = 2_000_000_000
DEVICE = "SYNTHETIC-MEMBER-DEVICE"
MASTER = base64.b64encode(bytes(range(32))).decode("ascii")
TOKEN_A = "v1|synthetic-A|1785500000|1788092000|r." + "a" * 64
TOKEN_B = "v1|synthetic-B|1785500000|1788092000|r." + "b" * 64


def member_lease(kind="quota", *, revision=3):
    plan = "30d" if kind == "quota" else "90d"
    cutoff = NOW + 120 if kind == "quota" else NOW + 20
    pet = {"kind": kind, "status": "available" if kind == "quota" else "active",
           "server_now": NOW, "duration_days": 30 if kind == "quota" else 90, "entitlement_exp": cutoff}
    if kind == "quota":
        pet.update(total_seconds=129600, entitlement_id="a" * 64, usage_revision=5,
                   remaining_seconds=120000, last_seq=2, visible=False, visibility_lease_exp=NOW, status="paused")
    return {"uid": "synthetic-A", "device": DEVICE, "exp": NOW + 60, "paid_exp": NOW + 300,
            "plan_kind": plan, "feature_entitlements": {"pet_companion": kind},
            "feature_usage": {"schema_version": 2, "pet_companion": pet},
            "membership": {"schema_version": 1, "revision": revision, "plan_exp": NOW + 120,
                           "my_records_exp": cutoff if kind == "full" else None,
                           "private_dashboard_exp": None, "credential_uid": "synthetic-A", "canonical_uid": "synthetic-A"}}


def parse(lease):
    return client._validated_activation_fields(lease=lease, master_key_b64=MASTER, mode="paid",
                                             device=DEVICE, now=NOW, signed_envelope_valid=True)


@pytest.mark.parametrize("kind", ["quota", "full"])
def test_new_bank_and_independent_full_deadline_are_valid_not_inferred_full(kind):
    fields = parse(member_lease(kind))
    assert fields is not None
    assert fields["feature_entitlements"] == {"pet_companion": kind}
    assert fields["feature_usage"]["pet_companion"]["kind"] == kind


def test_membership_canonical_mismatch_is_not_a_valid_main_grant():
    lease = member_lease()
    lease["membership"]["canonical_uid"] = "synthetic-other"
    # Keep a fully valid legacy Pet shape so the old parser's failure is
    # specifically ignoring membership identity, not the new schema.
    lease["feature_usage"]["schema_version"] = 1
    pet = lease["feature_usage"]["pet_companion"]
    pet.update(total_seconds=43200, remaining_seconds=30000, entitlement_exp=lease["paid_exp"])
    assert parse(lease) is None


@pytest.fixture()
def runtime(isolated_activation_runtime, monkeypatch):
    # Existing temporary-only real commit/provider fixture; no server imports.
    isolated_activation_runtime.write_text(TOKEN_A, encoding="utf-8")
    monkeypatch.setattr(client, "device_fingerprint", lambda: DEVICE)
    monkeypatch.setattr(client.time, "time", lambda: NOW)
    monkeypatch.setattr(enforce.time, "time", lambda: NOW)
    monkeypatch.setattr(enforce, "_MEMBERSHIP_REVISION_FLOORS", {})
    monkeypatch.setattr(enforce, "_LAST_PET_USAGE_AUTHORITY", None)
    monkeypatch.setattr(enforce, "_LAST_PET_USAGE_AUTHORITY_SCHEMA", 1)
    monkeypatch.setattr(client, "_ACTIVATION_CLEAR_EPOCH", 0)
    monkeypatch.setattr(client, "_ACTIVATION_CLEAR_STATE", None)
    # Any accidental real carrier invocation must fail before a request.
    monkeypatch.setattr(client, "urlopen_verified", lambda *a, **k: pytest.fail("real HTTP forbidden"))
    return isolated_activation_runtime


def response(*, revision=3, status=None, credential="synthetic-A", kind="quota"):
    lease = member_lease(kind, revision=revision)
    lease["membership"]["credential_uid"] = credential
    body = {"ok": True, "mode": "paid", "lease": json.dumps(lease) + "." + "0" * 64,
            "master_key": MASTER, "membership_card_capability": {"schema_version": 1, "pet_quota_schema_version": 2}}
    if status is not None:
        body["card_redemption"] = {"schema_version": 1, "status": status, "receipt_id": "c" * 64,
            "credited_seconds": 30 * 86400, "receipt_revision": 2, "membership_revision": revision,
            "membership_exp": lease["paid_exp"], "pet_quota_added_seconds": 43200,
            "roaming_rebinds_added": 0, "committed": True}
    return 200, body


def seed(monkeypatch):
    monkeypatch.setattr(client, "_post_activate", lambda *a: response())
    initial = enforce.refresh_activation()
    assert initial.activated is True
    assert initial.feature_usage["pet_companion"]["total_seconds"] == 129600
    assert activation.data_key() == bytes(range(32))
    return initial


@pytest.mark.parametrize("status", ["redeemed", "replayed"])
def test_real_commit_keeps_a_and_complete_current_member_not_local_credit(runtime, monkeypatch, status):
    seed(monkeypatch)
    seen = []
    monkeypatch.setattr(client, "_post_activate", lambda *args: (seen.append(args) or response(status=status)))
    result = enforce.redeem_membership_card(card_token=TOKEN_B)
    assert result.activated is True and result.card_redemption["status"] == status
    assert seen[0][2] == TOKEN_A and seen[0][4] is None and seen[0][5] == TOKEN_B
    assert runtime.read_text(encoding="utf-8") == TOKEN_A
    assert client._load_cache(device=DEVICE, token=TOKEN_A)["lease"]["uid"] == "synthetic-A"
    assert result.paid_exp == NOW + 300  # not plus the receipt credit again
    assert result.feature_usage["pet_companion"]["remaining_seconds"] == 120000
    assert result.feature_entitlements == {"pet_companion": "quota"}
    assert enforce.current() is result and activation.data_key() == bytes(range(32))


@pytest.mark.parametrize("status", sorted(client._CARD_ACTION_STATUSES) + ["confirmation_pending"])
def test_typed_operation_failure_preserves_current_a_without_switch(runtime, monkeypatch, status):
    initial = seed(monkeypatch)
    pending = status == "confirmation_pending"
    reply = (503 if pending else 409, {"ok": False, "mode": "paid", "reason": "state_error" if pending else "card_action",
             "card_redemption": {"schema_version": 1, "status": status, "committed": None if pending else False}})
    monkeypatch.setattr(client, "_post_activate", lambda *args: reply)
    result = enforce.redeem_membership_card(card_token=TOKEN_B)
    assert result.activated is False and result.card_redemption["status"] == status
    assert result.card_redemption["committed"] is (None if pending else False)
    assert enforce.current() is initial and enforce.should_run_full() is True
    assert runtime.read_text(encoding="utf-8") == TOKEN_A and activation.data_key() == bytes(range(32))


@pytest.mark.parametrize("fault", ["missing_ack", "no_cap", "bool_cap", "bool_revision", "float_credit", "bad_receipt",
                                  "wrong_member_revision", "wrong_exp", "roaming_credit", "bad_pet_credit",
                                  "wrong_credential", "wrong_canonical", "unsigned", "http_failure", "transport", "pending_false"])
def test_bad_or_unknown_response_never_commits_renewal_or_replaces_a(runtime, monkeypatch, fault):
    initial = seed(monkeypatch)
    code, body = response(status="redeemed")
    ack = body["card_redemption"]
    if fault == "missing_ack": body.pop("card_redemption")
    elif fault == "no_cap": body.pop("membership_card_capability")
    elif fault == "bool_cap": body["membership_card_capability"]["schema_version"] = True
    elif fault == "bool_revision": ack["receipt_revision"] = True
    elif fault == "float_credit": ack["credited_seconds"] = float(ack["credited_seconds"])
    elif fault == "bad_receipt": ack["receipt_id"] = "C" * 64
    elif fault == "wrong_member_revision": ack["membership_revision"] = 9
    elif fault == "wrong_exp": ack["membership_exp"] += 1
    elif fault == "roaming_credit": ack["roaming_rebinds_added"] = 8
    elif fault == "bad_pet_credit": ack["pet_quota_added_seconds"] = 0
    elif fault in {"wrong_credential", "wrong_canonical"}:
        lease = member_lease()
        lease["membership"]["credential_uid" if fault == "wrong_credential" else "canonical_uid"] = "synthetic-B"
        body["lease"] = json.dumps(lease) + "." + "0" * 64
    elif fault == "unsigned": body["lease"] = body["lease"].rsplit(".", 1)[0] + ".sig"
    elif fault == "http_failure": code = 500
    elif fault == "pending_false":
        code, body = 503, {"ok": False, "mode": "paid", "reason": "state_error",
                          "card_redemption": {"schema_version": 1, "status": "confirmation_pending", "committed": False}}
    monkeypatch.setattr(client, "_post_activate", lambda *a: None if fault == "transport" else (code, body))
    writes = []
    original = client.commit_activation_candidate
    monkeypatch.setattr(client, "commit_activation_candidate", lambda candidate: (writes.append(True) or original(candidate)))
    result = enforce.redeem_membership_card(card_token=TOKEN_B)
    assert result.activated is False and not writes
    assert enforce.current() is initial and enforce.should_run_full() is True
    assert runtime.read_text(encoding="utf-8") == TOKEN_A


@pytest.mark.parametrize("reason", ["revoked", "device_revoked", "upgrade_required"])
def test_real_original_credential_denial_still_clears(runtime, monkeypatch, reason):
    seed(monkeypatch)
    monkeypatch.setattr(client, "_post_activate", lambda *a: (426 if reason == "upgrade_required" else 403,
                        {"ok": False, "mode": "paid", "reason": reason}))
    result = enforce.redeem_membership_card(card_token=TOKEN_B)
    assert result.reason == reason and enforce.current() is result
    assert enforce.should_run_full() is False and activation.is_activated() is False


def test_membership_revision_floor_before_real_cache_provider_commit(runtime, monkeypatch):
    initial = seed(monkeypatch)
    monkeypatch.setattr(client, "_post_activate", lambda *a: response(revision=2, status="replayed"))
    result = enforce.redeem_membership_card(card_token=TOKEN_B)
    assert result.reason == "membership_revision_rollback"
    assert enforce.current() is initial and runtime.read_text(encoding="utf-8") == TOKEN_A
    assert client._load_cache(device=DEVICE, token=TOKEN_A)["lease"]["membership"]["revision"] == 3


def test_confirmed_switch_uses_original_entry_b_credential_canonical_a(runtime, monkeypatch):
    seed(monkeypatch)
    monkeypatch.setattr(client, "_post_activate", lambda *a: response(credential="synthetic-B"))
    result = enforce.refresh_activation(ui_token=TOKEN_B)
    assert result.activated is True and runtime.read_text(encoding="utf-8").strip() == TOKEN_B
    assert result.membership["credential_uid"] == "synthetic-B"
    assert result.membership["canonical_uid"] == "synthetic-A"


@pytest.mark.parametrize("change", ["clear", "aba", "newer"])
def test_late_card_response_cannot_override_clear_aba_or_newer_response(runtime, monkeypatch, change):
    seed(monkeypatch)
    started, release = threading.Event(), threading.Event()
    def transport(*args):
        if len(args) == 6:
            started.set()
            assert release.wait(timeout=5)
            return response(status="redeemed")
        return response(revision=4)
    monkeypatch.setattr(client, "_post_activate", transport)
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(enforce.redeem_membership_card(card_token=TOKEN_B)))
    worker.start()
    try:
        assert started.wait(timeout=5)
        if change == "clear": client.clear_activation()
        elif change == "aba":
            assert client.save_token(TOKEN_B) is True
            assert client.save_token(TOKEN_A) is True
        else:
            newer = enforce.refresh_activation()
            assert newer.activated is True
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive() and outcomes[0].activated is False
    if change == "newer":
        assert enforce.current() is newer
        assert client._load_cache(device=DEVICE, token=TOKEN_A)["lease"]["membership"]["revision"] == 4
    else:
        assert not enforce.should_run_full()


def test_card_request_common_payload_keeps_b_independent_and_ordinary_shape():
    ordinary = client._activation_request_payload(device=DEVICE, token=TOKEN_A)
    assert "card_token" not in ordinary and "card_redemption_schema" not in ordinary
    payload = client._activation_request_payload(device=DEVICE, token=TOKEN_A, card_token=TOKEN_B)
    assert payload["token"] == TOKEN_A and payload["card_token"] == TOKEN_B and payload["card_redemption_schema"] == 1


def test_identity_bound_e2ee_carrier_preserves_same_current_credential(runtime, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "_post_activate_http_e2ee", lambda *a: (seen.append(a) or response(status="redeemed")))
    expected = client.activation_identity_snapshot()
    started, result = client._identity_bound_pet_usage_http_e2ee_post(object(), ui_token=None,
        expected_identity=expected, timeout=1, pet_usage_event=None, card_token=TOKEN_B)
    assert started is True and result[0] == 200
    assert seen[0][2] == TOKEN_A and seen[0][4] is None and seen[0][5] == TOKEN_B


@pytest.mark.parametrize("field,value", [("revision", True), ("plan_exp", None), ("my_records_exp", NOW + 301),
                                        ("private_dashboard_exp", 1.0), ("canonical_uid", 1)])
def test_membership_exact_types_and_feature_deadline_bounds(field, value):
    lease = member_lease()
    lease["membership"][field] = value
    assert parse(lease) is None


def test_advanced_membership_old_schema_is_pet_error_not_base_member_error():
    lease = member_lease()
    lease["feature_usage"]["schema_version"] = 1
    fields = parse(lease)
    assert fields is not None and fields["feature_entitlements"] == {"pet_companion": "none"}
    assert fields["feature_usage"]["pet_companion"]["status"] == "state_error"


def test_alias_cannot_lower_canonical_membership_revision_on_confirmed_switch(runtime, monkeypatch):
    seed(monkeypatch)
    monkeypatch.setattr(client, "_post_activate", lambda *a: response(revision=2, credential="synthetic-B"))
    result = enforce.refresh_activation(ui_token=TOKEN_B)
    assert result.reason == "membership_revision_rollback"
    assert runtime.read_text(encoding="utf-8") == TOKEN_A
    assert client._load_cache(device=DEVICE, token=TOKEN_A)["lease"]["membership"]["revision"] == 3


def test_known_member_cannot_fall_back_to_old_nonmember_success(runtime, monkeypatch):
    initial = seed(monkeypatch)
    _, body = response(status="redeemed")
    lease = member_lease()
    lease.pop("membership")
    body["lease"] = json.dumps(lease) + "." + "0" * 64
    monkeypatch.setattr(client, "_post_activate", lambda *a: (200, body))
    failed = enforce.redeem_membership_card(card_token=TOKEN_B)
    assert failed.activated is False and enforce.current() is initial
    assert runtime.read_text(encoding="utf-8") == TOKEN_A


@pytest.mark.parametrize("token", ["", "not-a-card", None])
def test_bad_card_or_no_active_member_does_not_start_network(runtime, monkeypatch, token):
    started = []
    monkeypatch.setattr(client, "_post_activate", lambda *a: (started.append(True) or response(status="redeemed")))
    if token is None:
        assert client.clear_activation()["can_activate"] is True
        result = enforce.redeem_membership_card(card_token=TOKEN_B)
    else:
        result = enforce.redeem_membership_card(card_token=token)
    assert result.activated is False and result.reason == "invalid_card_request" and not started


def test_renewal_installs_only_this_complete_online_upload_envelope(runtime, monkeypatch):
    seed(monkeypatch)
    code, body = response(status="redeemed")
    payload = {"schema_version": 1, "kind": "sample_upload", "subject_kind": "paid", "subject_id": "a" * 64,
        "uid_hash": "b" * 16, "device_alias": "c" * 64, "security_epoch": 7, "lease_id": "d" * 32,
        "iat": NOW, "exp": NOW + 30, "entitlement_exp": NOW + 300}
    envelope = json.dumps(payload, separators=(",", ":")) + "." + "0" * 64
    body["upload_lease"] = envelope
    monkeypatch.setattr(client, "_post_activate", lambda *a: (code, body))
    assert enforce.redeem_membership_card(card_token=TOKEN_B).activated is True
    assert client.current_upload_lease(now=NOW) == envelope
    assert "uid_hash" not in client._activation_request_payload(device=DEVICE, token=TOKEN_A, card_token=TOKEN_B)
    # A succeeding response without an upload lease cannot reuse an old envelope.
    monkeypatch.setattr(client, "_post_activate", lambda *a: response(status="replayed"))
    assert enforce.redeem_membership_card(card_token=TOKEN_B).activated is True
    assert client.current_upload_lease(now=NOW) is None


def test_current_roaming_metadata_preserved_not_replenished_or_guessed(runtime, monkeypatch):
    code, body = response(status="redeemed")
    lease = member_lease()
    lease["roaming_rebinds"] = {"schema_version": 1, "policy_epoch": 1, "limit": 8, "used": 8, "remaining": 0}
    body["lease"] = json.dumps(lease) + "." + "0" * 64
    monkeypatch.setattr(client, "_post_activate", lambda *a: (code, body))
    result = enforce.redeem_membership_card(card_token=TOKEN_B)
    assert result.activated is True and result.roaming_rebinds["used"] == 8 and result.roaming_rebinds["remaining"] == 0
    lease.pop("roaming_rebinds")
    body["lease"] = json.dumps(lease) + "." + "0" * 64
    assert enforce.redeem_membership_card(card_token=TOKEN_B).roaming_rebinds is None


@pytest.mark.parametrize("card", [None, True])
def test_public_entry_cannot_turn_missing_or_bool_card_into_ordinary_activation(runtime, monkeypatch, card):
    initial = seed(monkeypatch)
    monkeypatch.setattr(client, "_post_activate", lambda *a: pytest.fail("must not start network"))
    result = enforce.redeem_membership_card(card_token=card)
    assert result.activated is False and result.reason == "invalid_card_request"
    assert enforce.current() is initial and runtime.read_text(encoding="utf-8") == TOKEN_A


@pytest.mark.parametrize("days,kind", [(30, "quota"), (90, "full")])
def test_local_service_http_to_actual_current_public_pet_deadline(runtime, monkeypatch, tmp_path, days, kind):
    """Real local wire bodies/HMAC/parser/commit/provider; no socket/TLS.

    This test also runs via the external immutable-S2 runner. The gate byte is
    precreated for serial wire semantics, not a production startup/lock claim.
    """
    from test_membership_card_service_0_3_6 import _service, _token, MASTER as SERVICE_MASTER
    from test_membership_pet_deadline_0_3_6 import pet_view
    service = _service(tmp_path / "serial-wire-service")
    service._membership_gate_path.write_bytes(b"\0")
    a, b = _token("synthetic-A", 30), _token("synthetic-B", days)
    assert client.save_token(a) is True
    received = []
    class MemoryResponse:
        def __init__(self, code, body): self.code, self.body = code, body
        def getcode(self): return self.code
        def read(self, *args): return json.dumps(self.body).encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *args): return False
    def memory_http(request, timeout):
        payload = json.loads(request.data)
        received.append(payload)
        code, body = service.handle_activate(payload)
        return MemoryResponse(code, body)
    monkeypatch.setattr(client, "urlopen_verified", memory_http)
    initial = enforce.refresh_activation()
    assert initial.activated is True and initial.feature_usage_schema_version == 1
    result = enforce.redeem_membership_card(card_token=b)
    assert result.activated is True and result.feature_usage_schema_version == 2
    assert result.card_redemption["committed"] is True
    state = enforce.activation_state(now=NOW + 5)
    assert state["paid_exp"] == result.paid_exp
    actual = pet_view.pet_access_from_activation(state, now=NOW + 5)
    assert actual.usage_kind == kind and actual.allowed is (kind == "full")
    assert actual.can_start_usage is (kind == "quota")
    if kind == "quota":
        assert actual.total_seconds == 86400 and actual.seconds_left == 86400
    cutoff = result.feature_usage["pet_companion"]["entitlement_exp"]
    bad = pet_view.pet_access_from_activation(dict(state, paid_exp=cutoff - 1), now=NOW + 5)
    assert not bad.allowed and bad.status == "state_error"
    assert received[-1]["token"] == a and received[-1]["card_token"] == b
    assert runtime.read_text(encoding="utf-8").strip() == a and enforce.current() is result
    assert activation.data_key() == base64.b64decode(SERVICE_MASTER)
