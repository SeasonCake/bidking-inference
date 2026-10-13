"""Actual Pet view deadlines are not actual usage-meter deductions."""
import importlib.util
from pathlib import Path
import sys
import copy

import pytest
from bidking_lab.secure import client_activate as client, enforce
from test_membership_card_client_0_3_6 import member_lease, parse

spec = importlib.util.spec_from_file_location("membership_pet_view", Path(__file__).resolve().parents[1] / "hero_ref/tools/pet_access.py")
pet_view = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pet_view
spec.loader.exec_module(pet_view)
NOW = 2_000_000_000


@pytest.fixture(autouse=True)
def isolated_public_view(tmp_path, monkeypatch):
    """Explicit-result views must never consult a real local deny/token file."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for key in ("BIDKING_ACTIVATION_TOKEN", "BIDKING_ACTIVATION_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(client, "_package_dir", lambda: tmp_path / "empty-package")
    monkeypatch.setattr(client, "device_fingerprint", lambda: "SYNTHETIC-PUBLIC-VIEW")
    monkeypatch.setattr(enforce, "_activation_runtime_denied", lambda: False)


def full_state():
    return {"run_full": True, "mode": "paid", "reason": "ok", "plan_kind": "90d",
            "paid_exp": NOW + 300, "feature_usage_schema_version": 2,
            "feature_entitlements": {"pet_companion": "full"},
            "feature_usage": {"pet_companion": {"kind": "full", "status": "active", "server_now": NOW,
                "duration_days": 90, "entitlement_exp": NOW + 20}}}


@pytest.mark.parametrize("offset", [20, 21])
def test_full_pet_stops_at_feature_deadline_not_membership_deadline(offset, monkeypatch):
    monkeypatch.setattr(enforce, "_activation_runtime_denied", lambda: False)
    state = full_state()
    result = client.ActivationResult(activated=True, mode="paid", source="server", lease_exp=NOW + 60,
                                    paid_exp=NOW + 300, plan_kind="90d")
    assert enforce.should_run_full(result, now=NOW + offset) is True
    assert pet_view.pet_access_from_activation(state, now=NOW + 19).allowed is True
    assert pet_view.pet_access_from_activation(state, now=NOW + offset).allowed is False


def result_from_lease(lease):
    fields = parse(lease)
    return client.ActivationResult(activated=True, mode="paid", source="server", master_key=fields["master_key"],
        lease_exp=fields["exp"], paid_exp=fields["paid_exp"], plan_kind=fields["plan_kind"],
        membership=fields["membership"], feature_entitlements=fields["feature_entitlements"],
        feature_usage=fields["feature_usage"], feature_usage_schema_version=fields["feature_usage_schema_version"])


def view_state(result, now):
    # Actual public producer: never add a missing authority/deadline field in
    # the test adapter. The overlay forwards this dictionary unchanged.
    return enforce.activation_state(result, now=now)


def test_schema2_bank_actual_provider_projection_renderer_no_full_and_no_local_deduction(monkeypatch):
    monkeypatch.setattr(enforce, "_activation_runtime_denied", lambda: False)
    result = result_from_lease(member_lease())
    view = pet_view.pet_access_from_activation(view_state(result, NOW + 20), now=NOW + 20)
    assert view.status == "quota_paused" and view.usage_kind == "quota" and view.allowed is False
    assert view.total_seconds == 129600 and view.seconds_left == 120000
    assert pet_view.format_pet_access_clock(view) == "33:20:00"
    assert view.can_start_usage is True and view.can_persist_preferences is False
    assert enforce.should_run_full(result, now=NOW + 120) is True
    expired = pet_view.pet_access_from_activation(view_state(result, NOW + 120), now=NOW + 120)
    assert expired.allowed is False and expired.can_start_usage is False and expired.seconds_left is None


@pytest.mark.parametrize("offset", [14, 15, 16])
def test_visible_quota_stops_at_visibility_lease_not_by_local_usage_subtraction(offset):
    lease = member_lease()
    lease["feature_usage"]["pet_companion"].update(status="active", visible=True, visibility_lease_exp=NOW + 15)
    result = result_from_lease(lease)
    # Direct view and the actual enforce projection both consume actual now.
    direct = {"run_full": True, "mode": "paid", "reason": "ok", "paid_exp": result.paid_exp,
        "plan_kind": result.plan_kind, "feature_entitlements": result.feature_entitlements,
        "feature_usage": result.feature_usage, "feature_usage_schema_version": 2}
    for state in (direct, view_state(result, NOW + offset)):
        view = pet_view.pet_access_from_activation(state, now=NOW + offset)
        assert view.allowed is (offset < 15)
        assert view.seconds_left == (120000 if offset < 15 else None)


def test_full_feature_may_outlive_plan_exp_but_never_its_own_expiry():
    lease = member_lease("full")
    lease["membership"]["plan_exp"] = NOW + 10
    result = result_from_lease(lease)
    state = view_state(result, NOW + 11)
    assert state["membership_plan_refresh_required"] is True
    assert pet_view.pet_access_from_activation(state, now=NOW + 11).allowed is True
    assert pet_view.pet_access_from_activation(view_state(result, NOW + 20), now=NOW + 20).allowed is False


@pytest.mark.parametrize("fault", ["unknown", "state_error", "bank_not_multiple", "deadline_over_paid", "bool_schema"])
def test_pet_bad_or_unknown_remains_unknown_not_zero_or_full(fault):
    lease = member_lease()
    pet = lease["feature_usage"]["pet_companion"]
    if fault == "unknown": lease["feature_usage"] = None
    elif fault == "state_error":
        lease["feature_entitlements"] = {"pet_companion": "none"}
        lease["feature_usage"]["pet_companion"] = {"kind": "none", "status": "state_error", "server_now": NOW}
    elif fault == "bank_not_multiple": pet["total_seconds"] += 1
    elif fault == "deadline_over_paid": pet["entitlement_exp"] = lease["paid_exp"] + 1
    else: lease["feature_usage"]["schema_version"] = True
    result = result_from_lease(lease)
    view = pet_view.pet_access_from_activation(view_state(result, NOW), now=NOW)
    assert result.activated is True and view.allowed is False and view.seconds_left is None
    assert view.status == "state_error"


def guard(result, same_identity):
    return enforce._guard_pet_usage_revision(result, same_identity=same_identity, now=NOW,
        max_epoch=client.MAX_SAFE_ACTIVATION_EPOCH, result_type=client.ActivationResult,
        contract_projector=client.project_normalized_pet_contract)


@pytest.mark.parametrize("fault", ["revision", "balance_same_revision", "server_time", "entitlement_id"])
def test_schema2_bank_keeps_existing_revision_and_time_rollback_gates(monkeypatch, fault):
    monkeypatch.setattr(enforce, "_LAST_PET_USAGE_AUTHORITY", None)
    monkeypatch.setattr(enforce, "_LAST_PET_USAGE_AUTHORITY_SCHEMA", 1)
    initial = result_from_lease(member_lease())
    assert guard(initial, False) is initial
    changed = copy.deepcopy(initial)
    pet = changed.feature_usage["pet_companion"]
    if fault == "revision": pet["usage_revision"] -= 1
    elif fault == "balance_same_revision": pet["remaining_seconds"] -= 1
    elif fault == "server_time": pet["server_now"] -= 1
    else: pet["entitlement_id"] = "b" * 64
    rejected = guard(changed, True)
    assert rejected.activated is True and rejected.membership == initial.membership
    assert rejected.feature_entitlements == {"pet_companion": "none"}
    assert rejected.feature_usage["pet_companion"]["status"] == "state_error"


def test_higher_usage_revision_accepts_real_added_bank_without_inventing_remaining(monkeypatch):
    monkeypatch.setattr(enforce, "_LAST_PET_USAGE_AUTHORITY", None)
    monkeypatch.setattr(enforce, "_LAST_PET_USAGE_AUTHORITY_SCHEMA", 1)
    initial = result_from_lease(member_lease())
    guard(initial, False)
    changed = copy.deepcopy(initial)
    changed.feature_usage["pet_companion"].update(usage_revision=6, total_seconds=172800, remaining_seconds=120001)
    assert guard(changed, True) is changed
    assert changed.feature_usage["pet_companion"]["remaining_seconds"] == 120001


@pytest.mark.parametrize("kind", ["quota", "full"])
def test_legacy_schema1_strict_positive_and_custom_not_relaxed(kind):
    lease = member_lease(kind)
    lease.pop("membership")
    lease["feature_usage"]["schema_version"] = 1
    pet = lease["feature_usage"]["pet_companion"]
    pet["entitlement_exp"] = lease["paid_exp"]
    if kind == "quota": pet.update(total_seconds=43200, remaining_seconds=30000)
    result = result_from_lease(lease)
    assert result.feature_usage_schema_version == 1
    assert pet_view.pet_access_from_activation(view_state(result, NOW), now=NOW).usage_kind == kind
    if kind == "quota":
        pet["total_seconds"] = 129600
        assert parse(lease)["feature_entitlements"] == {"pet_companion": "none"}


@pytest.mark.parametrize("kind", ["quota", "full"])
def test_actual_public_paid_deadline_and_strict_consumer_boundary(kind):
    result = result_from_lease(member_lease(kind))
    state = enforce.activation_state(result, now=NOW + 5)
    assert state["paid_exp"] == result.paid_exp and type(state["paid_exp"]) is int
    actual = pet_view.pet_access_from_activation(state, now=NOW + 5)
    assert actual.usage_kind == kind
    assert actual.allowed is (kind == "full")
    assert actual.can_start_usage is (kind == "quota")
    cutoff = result.feature_usage["pet_companion"]["entitlement_exp"]
    bad = dict(state, paid_exp=cutoff - 1)
    assert pet_view.pet_access_from_activation(bad, now=NOW + 5).status == "state_error"
    # Public projection does not turn feature expiry into base expiry.
    expired = enforce.activation_state(result, now=cutoff)
    assert expired["run_full"] is True and expired["paid_exp"] == result.paid_exp
    assert pet_view.pet_access_from_activation(expired, now=cutoff).allowed is False


def test_legacy_permanent_public_deadline_remains_none():
    result = client.ActivationResult(activated=True, mode="paid", source="server", lease_exp=NOW + 60,
        paid_exp=None, plan_kind="legacy_permanent", feature_entitlements={"pet_companion": "full"},
        feature_usage={"pet_companion": {"kind": "full", "status": "active", "server_now": NOW}})
    state = enforce.activation_state(result, now=NOW + 5)
    assert state["paid_exp"] is None
    assert pet_view.pet_access_from_activation(state, now=NOW + 5).allowed is True
