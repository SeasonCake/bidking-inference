"""Synthetic store controls only: no keys, signed tokens, account files or network."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import membership_card_store as store  # noqa: E402

NOW = 1_800_000_000
DAY = store.DAY_SECONDS
POLICY = store.SELECTED_PURCHASE_POLICY


def _card(uid: str, days: int | None = 30, permission: str = "bound", *, variant: str = "one", usage: str = "unused") -> store.VerifiedCard:
    # SHA is just a fixture stand-in for a server-verified canonical payload.
    return store.VerifiedCard(uid, hashlib.sha256((uid + "-" + variant).encode()).hexdigest(),
                              None if days is None else days * DAY, permission, usage)


def _seed(uid: str = "fixture-A", days: int | None = 30, *, elapsed: int = 15,
          permission: str = "bound", used: int = 14_400, rebinds: int = 5) -> store.ValidatedMembershipSeed:
    card = _card(uid, days, permission, usage="activated")
    start = NOW - elapsed * DAY
    end = None if days is None else start + days * DAY
    segments = () if card.plan_kind is None else (store.BenefitSegment(card.plan_kind, start, end, card.payload_fingerprint),)
    quota = 43_200 if days == 30 else 0
    return store.ValidatedMembershipSeed(uid, card, start, end, segments,
        store.BindingObservation("fixture-binding", rebinds, "fixture-existing-policy-not-deployment-proof"),
        quota, min(used, quota), "fixture-opaque-pet-ledger", NOW, True)


def _new(tmp_path: Path) -> store.MembershipCardStore:
    path = tmp_path / "authority.sqlite3"
    store.MembershipCardStore.initialize(path)
    return store.MembershipCardStore(path, timeout_seconds=2)


def _rows(path: Path) -> tuple:
    with sqlite3.connect(path) as connection:
        return tuple((table, tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1")))
                     for table in ("accounts", "card_claims"))


@pytest.mark.parametrize("old_days,new_days,left,plan,full_left,private_left", [
    (30, 30, 45, "30d", None, None),
    (30, 90, 105, "90d", 105, None),
    (90, 30, 45, "90d", 15, None),
    (365, 90, 105, "365d", 105, 15),
    (365, 365, 380, "365d", 380, 380),
])
def test_selected_tier_and_feature_rules(tmp_path, old_days, new_days, left, plan, full_left, private_left):
    authority = _new(tmp_path)
    seed = _seed(days=old_days, elapsed=old_days - 15)
    card = _card("fixture-B", new_days)
    result = authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY, legacy_seed=seed)
    assert result.status is store.OperationStatus.REDEEMED
    snap = result.snapshot
    assert snap.account_uid == seed.account_uid
    assert (snap.membership_exp - NOW) // DAY == left
    assert snap.current_plan_kind == plan
    assert snap.full_pet_exp == (None if full_left is None else NOW + full_left * DAY)
    assert snap.my_records_exp == snap.full_pet_exp
    assert snap.private_dashboard_exp == (None if private_left is None else NOW + private_left * DAY)
    assert snap.preserved_binding_observation == seed.binding_observation
    assert snap.pet_preserved_used_seconds == seed.pet_quota_used_seconds


def test_90_to_30_exact_feature_boundary_does_not_end_membership(tmp_path):
    authority = _new(tmp_path)
    seed = _seed(days=90, elapsed=75)
    authority.redeem(seed.account_uid, _card("fixture-B"), now=NOW, policy=POLICY, legacy_seed=seed)
    before = authority.read(seed.account_uid, now=NOW + 15 * DAY - 1)
    boundary = authority.read(seed.account_uid, now=NOW + 15 * DAY)
    assert before.current_plan_kind == "90d" and before.full_pet_exp == NOW + 15 * DAY
    assert boundary.current_plan_kind == "30d" and boundary.full_pet_exp is None
    assert boundary.my_records_exp is None and boundary.membership_exp == NOW + 45 * DAY
    assert boundary.pet_purchased_quota_seconds == 43_200
    assert authority.read(seed.account_uid, now=NOW + 45 * DAY).current_plan_kind is None


def test_three_30_purchases_do_not_become_90_plan(tmp_path):
    authority = _new(tmp_path)
    seed = _seed(elapsed=0)
    authority.record_first_activation(seed, now=NOW)
    for suffix in ("B", "C"):
        authority.redeem(seed.account_uid, _card("fixture-" + suffix), now=NOW, policy=POLICY)
    snap = authority.read(seed.account_uid, now=NOW)
    assert snap.membership_exp == NOW + 90 * DAY
    assert snap.current_plan_kind == "30d" and snap.full_pet_exp is None
    assert snap.my_records_exp is None and snap.private_dashboard_exp is None
    assert snap.pet_purchased_quota_seconds == 3 * 43_200
    assert snap.pet_preserved_used_seconds == 14_400


def test_replay_receipt_stable_latest_snapshot_separate_quota_carry(tmp_path):
    authority = _new(tmp_path)
    seed, card = _seed(), _card("fixture-B")
    first = authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY, legacy_seed=seed)
    saved = _rows(authority.path)
    replay = authority.redeem(seed.account_uid, card, now=NOW + 1, policy=POLICY)
    assert replay.status is store.OperationStatus.REPLAYED and replay.receipt == first.receipt
    assert _rows(authority.path) == saved
    assert replay.snapshot.pet_purchased_quota_seconds == 86_400
    assert replay.snapshot.pet_preserved_used_seconds == 14_400
    authority.redeem(seed.account_uid, _card("fixture-C", 90), now=NOW + 1, policy=POLICY)
    later = authority.redeem(seed.account_uid, card, now=NOW + 2, policy=POLICY)
    assert later.receipt == first.receipt and later.snapshot.revision == 3
    assert later.snapshot.membership_exp > later.receipt.membership_exp
    assert later.snapshot.pet_purchased_quota_seconds == 86_400


def test_roaming_renewal_adds_zero_and_does_not_assert_a_limit(tmp_path):
    authority = _new(tmp_path)
    # The store does not invent an 8-limit: this explicit historical observation
    # can exceed it and must be preserved rather than silently repaired/reset.
    seed = _seed(permission="roaming", rebinds=11)
    result = authority.redeem(seed.account_uid, _card("fixture-B", 90, "roaming"),
                              now=NOW, policy=POLICY, legacy_seed=seed)
    assert result.snapshot.preserved_binding_observation.rebinds_used == 11
    assert result.receipt.roaming_rebinds_added == 0
    assert result.snapshot.purchase_grants[0].roaming_rebinds_added == 0


def test_deployed_v5_unrecorded_rebind_counter_remains_unknown(tmp_path):
    authority = _new(tmp_path)
    seed = _seed(permission="roaming")
    seed = replace(seed, binding_observation=store.BindingObservation(
        "fixture-v5-legacy-string-binding", None, "deployed-v5-no-recorded-counter"))
    result = authority.redeem(seed.account_uid, _card("fixture-B", 90, "roaming"),
                              now=NOW, policy=POLICY, legacy_seed=seed)
    assert result.snapshot.preserved_binding_observation == seed.binding_observation
    assert result.snapshot.preserved_binding_observation.rebinds_used is None
    assert result.receipt.roaming_rebinds_added == 0


@pytest.mark.parametrize("seed,card,status", [
    (_seed(), _card("fixture-B", 90, "roaming"), store.OperationStatus.SWITCH_PERMISSION),
    (_seed(permission="roaming"), _card("fixture-B"), store.OperationStatus.SWITCH_PERMISSION),
    (_seed(), _card("fixture-B", None), store.OperationStatus.SWITCH_PERMANENT),
    (_seed(days=None), _card("fixture-B"), store.OperationStatus.SWITCH_PERMANENT),
    (_seed(elapsed=31), _card("fixture-B"), store.OperationStatus.UNSUPPORTED_EXPIRED),
    (_seed(), _card("fixture-B", 7), store.OperationStatus.UNSUPPORTED_PLAN),
    (_seed(days=7, elapsed=0), _card("fixture-B"), store.OperationStatus.UNSUPPORTED_PLAN),
])
def test_typed_unsupported_operation_does_not_import_or_consume(tmp_path, seed, card, status):
    authority = _new(tmp_path)
    before = _rows(authority.path)
    result = authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY, legacy_seed=seed)
    assert result.status is status and result.receipt is None
    assert _rows(authority.path) == before
    assert authority.lookup_claim(card) is None


def test_first_activation_claim_blocks_renewal_but_is_replayable(tmp_path):
    authority = _new(tmp_path)
    used = _seed("fixture-B", 90, elapsed=0)
    first = authority.record_first_activation(used, now=NOW)
    replay = authority.record_first_activation(used, now=NOW + 1)
    assert first.status is store.OperationStatus.FIRST_ACTIVATED
    assert replay.receipt == first.receipt
    before = _rows(authority.path)
    target = _seed()
    result = authority.redeem(target.account_uid, used.original_card, now=NOW, policy=POLICY, legacy_seed=target)
    assert result.status is store.OperationStatus.SWITCH_USED_CARD
    assert result.receipt is None and result.snapshot is None
    assert _rows(authority.path) == before and authority.read(target.account_uid, now=NOW) is None


@pytest.mark.parametrize("usage,status", [("activated", store.OperationStatus.SWITCH_USED_CARD),
                                         ("unknown", store.OperationStatus.UNSUPPORTED_USAGE)])
def test_legacy_usage_must_not_be_guessed_from_absent_claim(tmp_path, usage, status):
    authority = _new(tmp_path)
    before = _rows(authority.path)
    result = authority.redeem("fixture-A", _card("fixture-B", usage=usage), now=NOW,
                              policy=POLICY, legacy_seed=_seed())
    assert result.status is status and result.receipt is None
    assert _rows(authority.path) == before and authority.lookup_claim(_card("fixture-B")) is None


def test_same_card_refresh_after_adapter_reports_consumed_is_stable(tmp_path):
    authority = _new(tmp_path)
    card = _card("fixture-B")
    first = authority.redeem("fixture-A", card, now=NOW, policy=POLICY, legacy_seed=_seed())
    replay = authority.redeem("fixture-A", replace(card, legacy_use_state="activated"), now=NOW + 1, policy=POLICY)
    assert replay.status is store.OperationStatus.REPLAYED and replay.receipt == first.receipt


@pytest.mark.parametrize("used", [None, 0, 43_200])
def test_pet_usage_observation_unknown_is_not_zero_or_a_balance(tmp_path, used):
    authority = _new(tmp_path)
    seed = replace(_seed(), pet_quota_used_seconds=used)
    result = authority.redeem(seed.account_uid, _card("fixture-B"), now=NOW, policy=POLICY, legacy_seed=seed)
    assert type(result.snapshot.pet_preserved_used_seconds) is type(used)
    assert result.snapshot.pet_preserved_used_seconds == used
    assert result.snapshot.pet_purchased_quota_seconds == 86_400
    with sqlite3.connect(authority.path) as connection:
        doc = json.loads(connection.execute("SELECT document FROM accounts").fetchone()[0])
    assert type(doc["pet_quota_used_seconds"]) is type(used) and doc["pet_quota_used_seconds"] == used
    observed = authority.read(seed.account_uid, now=NOW).pet_preserved_used_seconds
    assert type(observed) is type(used) and observed == used


@pytest.mark.parametrize("used", [-1, 43_201, True, False, 0.0, "0", "unknown"])
def test_pet_usage_observation_invalid_exact_type_and_bounds(tmp_path, used):
    authority = _new(tmp_path)
    before = _rows(authority.path)
    with pytest.raises(store.MembershipStateError):
        authority.redeem("fixture-A", _card("fixture-B"), now=NOW, policy=POLICY,
                         legacy_seed=replace(_seed(), pet_quota_used_seconds=used))
    assert _rows(authority.path) == before


def test_claim_inventory_is_read_only_and_validates_complete_lineage(tmp_path):
    authority = _new(tmp_path)
    assert authority.claims_for_account("fixture-missing") == ()
    authority.redeem("fixture-A", _card("fixture-B"), now=NOW, policy=POLICY, legacy_seed=_seed())
    authority.redeem("fixture-A", _card("fixture-C", 90), now=NOW + 1, policy=POLICY)
    before = _rows(authority.path)
    claims = authority.claims_for_account("fixture-A")
    assert type(claims) is tuple
    assert [c.card_uid for c in claims] == ["fixture-A", "fixture-B", "fixture-C"]
    assert [c.revision for c in claims] == [1, 2, 3]
    assert _rows(authority.path) == before
    with sqlite3.connect(authority.path) as connection:
        connection.execute("DELETE FROM card_claims WHERE card_uid='fixture-B'")
    with pytest.raises(store.MembershipStateError):
        authority.claims_for_account("fixture-A")


@pytest.mark.parametrize("corrupt_used", ["absent", -1, 43_201, True, "0"])
def test_unknown_usage_readback_does_not_relax_schema_or_bounds(tmp_path, corrupt_used):
    authority = _new(tmp_path)
    authority.redeem("fixture-A", _card("fixture-B"), now=NOW, policy=POLICY,
                     legacy_seed=replace(_seed(), pet_quota_used_seconds=None))
    with sqlite3.connect(authority.path) as connection:
        doc = json.loads(connection.execute("SELECT document FROM accounts").fetchone()[0])
        if corrupt_used == "absent":
            del doc["pet_quota_used_seconds"]
        else:
            doc["pet_quota_used_seconds"] = corrupt_used
        connection.execute("UPDATE accounts SET document=?", (json.dumps(doc),))
    with pytest.raises(store.MembershipStateError):
        authority.read("fixture-A", now=NOW)


def test_redeemed_card_cannot_start_an_independent_member(tmp_path):
    authority = _new(tmp_path)
    seed, card = _seed(), _card("fixture-B", 90)
    result = authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY, legacy_seed=seed)
    before = _rows(authority.path)
    attempted = authority.record_first_activation(_seed("fixture-B", 90, elapsed=0), now=NOW)
    assert attempted.status is store.OperationStatus.CARD_UNAVAILABLE
    assert _rows(authority.path) == before
    assert authority.lookup_claim(card).account_uid == seed.account_uid
    assert authority.lookup_claim(seed.original_card).account_uid == seed.account_uid
    assert authority.read("fixture-B", now=NOW) is None


def test_cross_account_card_result_does_not_leak_owner(tmp_path):
    authority = _new(tmp_path)
    seed, card = _seed(), _card("fixture-C", 90)
    authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY, legacy_seed=seed)
    other = _seed("fixture-private-other")
    before = _rows(authority.path)
    result = authority.redeem(other.account_uid, card, now=NOW, policy=POLICY, legacy_seed=other)
    assert result == store.OperationResult(store.OperationStatus.CARD_UNAVAILABLE)
    assert "fixture-A" not in repr(result) and _rows(authority.path) == before


@pytest.mark.parametrize("collision", ["uid", "fingerprint", "seed_fingerprint"])
def test_double_identity_protection(tmp_path, collision):
    authority = _new(tmp_path)
    seed, card = _seed(), _card("fixture-B")
    if collision == "seed_fingerprint":
        collided = replace(card, payload_fingerprint=seed.original_card.payload_fingerprint)
    else:
        authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY, legacy_seed=seed)
        collided = replace(card, payload_fingerprint=_card("fixture-X").payload_fingerprint) if collision == "uid" else replace(card, card_uid="fixture-X")
    before = _rows(authority.path)
    result = authority.redeem(seed.account_uid, collided, now=NOW, policy=POLICY, legacy_seed=seed)
    assert result.status is store.OperationStatus.UNSUPPORTED_IDENTITY
    assert _rows(authority.path) == before


def test_multiple_upgrade_downgrade_chain_preserves_source_periods(tmp_path):
    authority = _new(tmp_path)
    seed = _seed(days=365, elapsed=350)
    authority.redeem(seed.account_uid, _card("fixture-B", 90), now=NOW, policy=POLICY, legacy_seed=seed)
    authority.redeem(seed.account_uid, _card("fixture-C", 30), now=NOW + DAY, policy=POLICY)
    snap = authority.read(seed.account_uid, now=NOW + DAY)
    assert snap.membership_exp == NOW + 135 * DAY
    assert snap.private_dashboard_exp == NOW + 15 * DAY
    assert snap.full_pet_exp == NOW + 105 * DAY
    authority.redeem(seed.account_uid, _card("fixture-D", 365), now=NOW + 16 * DAY, policy=POLICY)
    snap = authority.read(seed.account_uid, now=NOW + 16 * DAY)
    assert snap.membership_exp == NOW + 500 * DAY and snap.private_dashboard_exp == snap.membership_exp
    authority.redeem(seed.account_uid, _card("fixture-E", 30), now=NOW + 17 * DAY, policy=POLICY)
    snap = authority.read(seed.account_uid, now=NOW + 17 * DAY)
    assert snap.membership_exp == NOW + 530 * DAY and snap.private_dashboard_exp == NOW + 500 * DAY
    assert authority.read(seed.account_uid, now=NOW + 500 * DAY).current_plan_kind == "30d"


@pytest.mark.parametrize("attack", ["early_downgrade", "late_upgrade"])
def test_pointwise_replay_rejects_plausible_wrong_business_periods(tmp_path, attack):
    authority = _new(tmp_path)
    old_days, new_days = (90, 30) if attack == "early_downgrade" else (30, 90)
    seed = _seed(days=old_days, elapsed=old_days - 15)
    card = _card("fixture-B", new_days)
    authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY, legacy_seed=seed)
    with sqlite3.connect(authority.path) as connection:
        doc = json.loads(connection.execute("SELECT document FROM accounts").fetchone()[0])
        boundary = NOW if attack == "early_downgrade" else seed.membership_exp
        # Both spans are individually within the real sources' allowed windows.
        # Only replaying the actual purchase order exposes the business forgery.
        doc["benefit_segments"] = [
            asdict(store.BenefitSegment(seed.original_card.plan_kind, seed.started_at, boundary,
                                       seed.original_card.payload_fingerprint)),
            asdict(store.BenefitSegment(card.plan_kind, boundary, doc["membership_exp"], card.payload_fingerprint)),
        ]
        connection.execute("UPDATE accounts SET document=?", (json.dumps(doc),))
    with pytest.raises(store.MembershipStateError):
        authority.read(seed.account_uid, now=NOW)


@pytest.mark.parametrize("phase,exception", [("before_commit", store.MembershipStateError),
                                            ("after_commit", store.MembershipCommitUnknownError)])
def test_failure_before_rollback_after_recover(tmp_path, phase, exception):
    authority = _new(tmp_path)
    seed, card = _seed(), _card("fixture-B", 90)
    authority.record_first_activation(seed, now=NOW)
    before = _rows(authority.path)

    def fail(seen):
        if seen == phase:
            raise RuntimeError("synthetic failure")

    failing = store.MembershipCardStore(authority.path, fault_hook=fail)
    with pytest.raises(exception):
        failing.redeem(seed.account_uid, card, now=NOW, policy=POLICY)
    if phase == "before_commit":
        assert _rows(authority.path) == before and authority.lookup_claim(card) is None
        recovered = authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY)
        assert recovered.status is store.OperationStatus.REDEEMED
    else:
        recovered = authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY)
        assert recovered.status is store.OperationStatus.REPLAYED
    assert recovered.snapshot.membership_exp == NOW + 105 * DAY
    assert len(recovered.snapshot.purchase_grants) == 1


def test_failed_legacy_import_rolls_back_both_cards_and_seed(tmp_path):
    authority = _new(tmp_path)
    before = _rows(authority.path)
    failing = store.MembershipCardStore(authority.path, fault_hook=lambda _: (_ for _ in ()).throw(RuntimeError("synthetic")))
    with pytest.raises(store.MembershipStateError):
        failing.redeem("fixture-A", _card("fixture-B"), now=NOW, policy=POLICY, legacy_seed=_seed())
    assert _rows(authority.path) == before


@pytest.mark.parametrize("change", [
    {"validation_complete": False}, {"validated_at": NOW + 1}, {"started_at": True},
    {"benefit_segments": ()}, {"membership_exp": NOW + 999},
    {"pet_quota_used_seconds": 43_201}, {"pet_quota_total_seconds": 99},
])
def test_explicit_complete_seed_not_guessed(tmp_path, change):
    authority = _new(tmp_path)
    before = _rows(authority.path)
    with pytest.raises(store.MembershipStateError):
        authority.redeem("fixture-A", _card("fixture-B"), now=NOW, policy=POLICY, legacy_seed=replace(_seed(), **change))
    assert _rows(authority.path) == before


def test_no_seed_and_invalid_policy_card_time_are_rejected_without_writes(tmp_path):
    authority = _new(tmp_path)
    before = _rows(authority.path)
    for kwargs in (
        {"legacy_seed": None}, {"legacy_seed": _seed(), "policy": replace(POLICY, roaming_extra_rebinds=8)},
        {"legacy_seed": _seed(), "policy": replace(POLICY, carry_unused_pet_quota=False)},
        {"legacy_seed": _seed(), "card": replace(_card("fixture-B"), duration_seconds=True)},
        {"legacy_seed": _seed(), "now": True},
    ):
        params = dict(card=_card("fixture-B"), now=NOW, policy=POLICY)
        params.update(kwargs)
        with pytest.raises(store.MembershipStateError):
            authority.redeem("fixture-A", **params)
        assert _rows(authority.path) == before


@pytest.mark.parametrize("corruption", ["version", "extra_schema", "clock", "duplicate_json", "missing_claim", "receipt_quota", "forged_plan", "forged_source_period", "header"])
def test_corruption_fails_closed(tmp_path, corruption):
    authority = _new(tmp_path)
    authority.redeem("fixture-A", _card("fixture-B"), now=NOW, policy=POLICY, legacy_seed=_seed())
    if corruption == "header":
        authority.path.write_bytes(b"synthetic invalid database")
    else:
        with sqlite3.connect(authority.path) as connection:
            if corruption == "version":
                connection.execute("PRAGMA user_version=2")
            elif corruption == "extra_schema":
                connection.execute("CREATE TABLE unexpected(x INTEGER)")
            elif corruption == "missing_claim":
                connection.execute("DELETE FROM card_claims WHERE card_uid='fixture-B'")
            elif corruption == "receipt_quota":
                raw = json.loads(connection.execute("SELECT receipt FROM card_claims WHERE card_uid='fixture-B'").fetchone()[0])
                raw["pet_quota_added_seconds"] = 99
                connection.execute("UPDATE card_claims SET receipt=? WHERE card_uid='fixture-B'", (json.dumps(raw),))
            else:
                raw = connection.execute("SELECT document FROM accounts").fetchone()[0]
                if corruption == "clock":
                    doc = json.loads(raw)
                    doc["membership_exp"] += 1
                    raw = json.dumps(doc)
                elif corruption in {"forged_plan", "forged_source_period"}:
                    doc = json.loads(raw)
                    if corruption == "forged_plan":
                        doc["benefit_segments"][0]["plan_kind"] = "365d"
                    else:
                        doc["benefit_segments"][-1]["source_fingerprint"] = doc["original_card"]["payload_fingerprint"]
                    raw = json.dumps(doc)
                else:
                    raw = raw.replace('"revision":2', '"revision":2,"revision":2')
                connection.execute("UPDATE accounts SET document=?", (raw,))
    with pytest.raises(store.MembershipStateError):
        store.MembershipCardStore(authority.path).read("fixture-A", now=NOW)


def test_initialize_never_overwrites_and_missing_does_not_create(tmp_path):
    authority = _new(tmp_path)
    before = authority.path.read_bytes()
    with pytest.raises(store.MembershipStateError):
        store.MembershipCardStore.initialize(authority.path)
    assert authority.path.read_bytes() == before
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(store.MembershipStateError):
        store.MembershipCardStore(missing)
    assert not missing.exists()


def test_unsupported_overflow_and_clock_rollback_do_not_write(tmp_path):
    authority = _new(tmp_path)
    limit = store.MAX_SAFE_EPOCH
    seed = _seed(days=365, elapsed=350)
    start, now = limit - 365 * DAY, limit - 15 * DAY
    seed = replace(seed, started_at=start, membership_exp=limit, validated_at=now,
                   benefit_segments=(store.BenefitSegment("365d", start, limit, seed.original_card.payload_fingerprint),))
    before = _rows(authority.path)
    result = authority.redeem(seed.account_uid, _card("fixture-B", 90), now=now, policy=POLICY, legacy_seed=seed)
    assert result.status is store.OperationStatus.UNSUPPORTED_OVERFLOW and _rows(authority.path) == before
    authority.redeem("fixture-A", _card("fixture-B"), now=NOW, policy=POLICY, legacy_seed=_seed())
    before = _rows(authority.path)
    with pytest.raises(store.MembershipStateError):
        authority.redeem("fixture-A", _card("fixture-C"), now=NOW - 1, policy=POLICY)
    assert _rows(authority.path) == before


def test_busy_deadline_is_bounded_and_original_member_unchanged(tmp_path):
    authority = _new(tmp_path)
    authority.record_first_activation(_seed(), now=NOW)
    before = _rows(authority.path)
    blocked = store.MembershipCardStore(authority.path, timeout_seconds=0.05)
    with sqlite3.connect(authority.path, isolation_level=None) as connection:
        connection.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(store.MembershipStateError):
            blocked.redeem("fixture-A", _card("fixture-B"), now=NOW, policy=POLICY)
        assert time.monotonic() - started < 1
        connection.execute("ROLLBACK")
    assert _rows(authority.path) == before


def _race(path: Path, tmp_path: Path, operations: list[tuple[str, str, str, int]]) -> list[str]:
    gate = tmp_path / "start-gate"
    processes = []
    deadline = time.monotonic() + 20
    try:
        for index, (mode, account, card_uid, days) in enumerate(operations):
            process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                str(path), str(gate), str(index), mode, account, card_uid, str(days)], cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            processes.append(process)
        while not all(Path(str(gate) + f".ready-{i}").exists() for i in range(len(processes))):
            if time.monotonic() > deadline or any(p.poll() is not None for p in processes):
                raise AssertionError("race workers did not reach bounded gate")
            time.sleep(0.01)
        gate.touch()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=max(0.1, deadline - time.monotonic()))
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout)["status"])
        return results
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_two_processes_two_accounts_one_card(tmp_path):
    authority = _new(tmp_path)
    for uid in ("fixture-A", "fixture-Z"):
        authority.record_first_activation(_seed(uid), now=NOW)
    statuses = _race(authority.path, tmp_path, [
        ("redeem", "fixture-A", "fixture-B", 90), ("redeem", "fixture-Z", "fixture-B", 90)])
    assert sorted(statuses) == ["card_unavailable", "redeemed"]
    snapshots = [authority.read(uid, now=NOW) for uid in ("fixture-A", "fixture-Z")]
    assert sorted((s.membership_exp - NOW) // DAY for s in snapshots) == [15, 105]
    assert sum(len(s.purchase_grants) for s in snapshots) == 1


def test_two_processes_same_account_same_card(tmp_path):
    authority = _new(tmp_path)
    authority.record_first_activation(_seed(), now=NOW)
    statuses = _race(authority.path, tmp_path, [("redeem", "fixture-A", "fixture-B", 30)] * 2)
    assert sorted(statuses) == ["redeemed", "replayed"]
    snap = authority.read("fixture-A", now=NOW)
    assert snap.membership_exp == NOW + 45 * DAY and snap.pet_purchased_quota_seconds == 86_400
    assert len(snap.purchase_grants) == 1


def test_two_processes_different_cards_do_not_lose_updates(tmp_path):
    authority = _new(tmp_path)
    authority.record_first_activation(_seed(), now=NOW)
    statuses = _race(authority.path, tmp_path, [
        ("redeem", "fixture-A", "fixture-B", 30), ("redeem", "fixture-A", "fixture-C", 90)])
    assert statuses == ["redeemed", "redeemed"]
    snap = authority.read("fixture-A", now=NOW)
    assert snap.membership_exp == NOW + 135 * DAY and snap.revision == 3
    assert snap.current_plan_kind == "90d" and snap.pet_purchased_quota_seconds == 86_400
    assert snap.full_pet_exp in {NOW + 105 * DAY, NOW + 135 * DAY}  # Valid serialized purchase order.


def test_two_processes_first_use_races_redemption(tmp_path):
    authority = _new(tmp_path)
    authority.record_first_activation(_seed(), now=NOW)
    statuses = _race(authority.path, tmp_path, [
        ("redeem", "fixture-A", "fixture-B", 90), ("first", "fixture-B", "fixture-B", 90)])
    assert set(statuses) in ({"redeemed", "card_unavailable"}, {"first_activated", "switch_required_used_card"})
    claim = authority.lookup_claim(_card("fixture-B", 90))
    if claim.operation == "redeem":
        assert authority.read("fixture-B", now=NOW) is None
        assert authority.read("fixture-A", now=NOW).membership_exp == NOW + 105 * DAY
    else:
        assert authority.read("fixture-A", now=NOW).membership_exp == NOW + 15 * DAY
        assert authority.read("fixture-B", now=NOW).membership_exp == NOW + 90 * DAY


def test_claim_for_card_uid_is_exact_readonly_and_not_redemption_qualification(tmp_path):
    authority = _new(tmp_path)
    seed, card = _seed(), _card("fixture-B", 90)
    result = authority.redeem(seed.account_uid, card, now=NOW, policy=POLICY, legacy_seed=seed)
    before = _rows(authority.path)
    assert authority.claim_for_card_uid("fixture-B") == result.receipt
    assert authority.claim_for_card_uid("fixture-A").operation == "first_activation"
    assert authority.claim_for_card_uid("fixture-B-suffix") is None
    assert authority.claim_for_card_uid("fixture-unclaimed") is None
    assert _rows(authority.path) == before
    assert authority.redeem("fixture-Z", card, now=NOW, policy=POLICY).status is store.OperationStatus.CARD_UNAVAILABLE
    assert _rows(authority.path) == before


def test_claim_for_card_uid_rejects_corrupt_owner_lineage_not_just_matching_receipt(tmp_path):
    authority = _new(tmp_path)
    authority.redeem("fixture-A", _card("fixture-B", 90), now=NOW, policy=POLICY, legacy_seed=_seed())
    with sqlite3.connect(authority.path) as connection:
        connection.execute("DELETE FROM card_claims WHERE card_uid=?", ("fixture-A",))
    with pytest.raises(store.MembershipStateError, match="incomplete membership claims"):
        authority.claim_for_card_uid("fixture-B")


def _worker(argv: list[str]) -> None:
    path, gate_raw, index, mode, account, card_uid, days_raw = argv
    gate = Path(gate_raw)
    authority = store.MembershipCardStore(Path(path), timeout_seconds=5)
    Path(str(gate) + ".ready-" + index).touch()
    deadline = time.monotonic() + 15
    while not gate.exists():
        if time.monotonic() > deadline:
            raise RuntimeError("synthetic worker gate timeout")
        time.sleep(0.01)
    days = int(days_raw)
    if mode == "redeem":
        result = authority.redeem(account, _card(card_uid, days), now=NOW, policy=POLICY)
    else:
        result = authority.record_first_activation(_seed(card_uid, days, elapsed=0), now=NOW)
    print(json.dumps({"status": result.status.value}))


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--worker":
    _worker(sys.argv[2:])
