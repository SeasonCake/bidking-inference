"""Transactional, server-adapter-only authority for prepaid membership cards.

This module is NOT wired into activation, payment, or any production process.
The adapter must verify both credentials, revocation, device binding and legacy
state before constructing these exact input objects. No signing key, raw token,
device fingerprint, legacy JSON or network is read here. A seed's binding/usage
values are preserved observations, not a replacement for the live binding/Pet
ledger. This store grants purchase credits; it does not meter Pet usage or assert
that any historical roaming limit was deployed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator

SCHEMA_VERSION = 1
APPLICATION_ID = 0x424D4331  # BMC1
MAX_SAFE_EPOCH = 253_402_300_799
DAY_SECONDS = 86_400
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PLANS = {30: "30d", 90: "90d", 365: "365d"}
_LEVEL = {"30d": 30, "90d": 90, "365d": 365}
_SCHEMA = """
CREATE TABLE accounts (
    account_uid TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    document TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE card_claims (
    card_uid TEXT PRIMARY KEY NOT NULL,
    payload_fingerprint TEXT NOT NULL UNIQUE,
    account_uid TEXT NOT NULL REFERENCES accounts(account_uid),
    operation TEXT NOT NULL CHECK (operation IN ('first_activation', 'redeem')),
    receipt TEXT NOT NULL
) WITHOUT ROWID;
"""


class MembershipStateError(RuntimeError):
    """Fixed fail-closed category; never include private input or exception text."""


class MembershipCommitUnknownError(MembershipStateError):
    """Commit is durable but confirmation failed; recover by the same card."""


class OperationStatus(str, Enum):
    REDEEMED = "redeemed"
    FIRST_ACTIVATED = "first_activated"
    REPLAYED = "replayed"
    SWITCH_PERMISSION = "switch_required_permissions"
    SWITCH_USED_CARD = "switch_required_used_card"
    SWITCH_PERMANENT = "switch_required_permanent"
    CARD_UNAVAILABLE = "card_unavailable"
    UNSUPPORTED_EXPIRED = "unsupported_expired_account"
    UNSUPPORTED_PLAN = "unsupported_plan"
    UNSUPPORTED_IDENTITY = "unsupported_card_identity"
    UNSUPPORTED_OVERFLOW = "unsupported_duration_overflow"
    UNSUPPORTED_USAGE = "unsupported_card_usage"


@dataclass(frozen=True)
class VerifiedCard:
    card_uid: str = field(repr=False)
    payload_fingerprint: str = field(repr=False)
    duration_seconds: int | None
    device_permission: str
    legacy_use_state: str

    @property
    def plan_kind(self) -> str | None:
        if self.duration_seconds is None:
            return None
        return _PLANS.get(self.duration_seconds // DAY_SECONDS) if self.duration_seconds % DAY_SECONDS == 0 else None


@dataclass(frozen=True)
class BindingObservation:
    binding_reference: str = field(repr=False)
    rebinds_used: int | None
    policy_reference: str


@dataclass(frozen=True)
class BenefitSegment:
    plan_kind: str
    starts_at: int
    ends_at: int
    source_fingerprint: str = field(repr=False)


@dataclass(frozen=True)
class ValidatedMembershipSeed:
    """Complete adapter-validated legacy snapshot, never inferred by this store."""
    account_uid: str = field(repr=False)
    original_card: VerifiedCard = field(repr=False)
    started_at: int
    membership_exp: int | None
    benefit_segments: tuple[BenefitSegment, ...]
    binding_observation: BindingObservation
    pet_quota_total_seconds: int
    pet_quota_used_seconds: int | None
    pet_ledger_reference: str = field(repr=False)
    validated_at: int
    validation_complete: bool


@dataclass(frozen=True)
class PurchaseGrantPolicy:
    """Explicit input; there is deliberately no implicit/default purchase promise."""
    policy_id: str
    pet_30_seconds: int
    carry_unused_pet_quota: bool
    roaming_extra_rebinds: int


SELECTED_PURCHASE_POLICY = PurchaseGrantPolicy(
    "author-20261011-call_32687c85ddca4eb48f846a46db874571", 43_200, True, 0
)


@dataclass(frozen=True)
class PurchaseGrant:
    grant_id: str
    source_fingerprint: str = field(repr=False)
    plan_kind: str
    purchased_seconds: int
    granted_at: int
    pet_quota_added_seconds: int
    carry_unused_pet_quota: bool
    roaming_rebinds_added: int
    policy_id: str


@dataclass(frozen=True)
class MembershipReceipt:
    receipt_id: str
    account_uid: str = field(repr=False)
    card_uid: str = field(repr=False)
    payload_fingerprint: str = field(repr=False)
    operation: str
    revision: int
    membership_exp: int
    credited_seconds: int
    grant_id: str | None
    pet_quota_added_seconds: int
    roaming_rebinds_added: int


@dataclass(frozen=True)
class MembershipSnapshot:
    account_uid: str = field(repr=False)
    revision: int
    membership_exp: int
    device_permission: str
    current_plan_kind: str | None
    current_plan_exp: int | None
    my_records_exp: int | None
    private_dashboard_exp: int | None
    full_pet_exp: int | None
    benefit_segments: tuple[BenefitSegment, ...]
    purchase_grants: tuple[PurchaseGrant, ...]
    preserved_binding_observation: BindingObservation
    pet_purchased_quota_seconds: int
    pet_preserved_used_seconds: int | None
    pet_ledger_reference: str = field(repr=False)


@dataclass(frozen=True)
class OperationResult:
    status: OperationStatus
    receipt: MembershipReceipt | None = None
    snapshot: MembershipSnapshot | None = None


def _text(value: object) -> str:
    if type(value) is not str or not value or len(value) > 256 or "\x00" in value:
        raise MembershipStateError("invalid membership text")
    return value


def _integer(value: object, *, minimum: int = 0, maximum: int = MAX_SAFE_EPOCH) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MembershipStateError("invalid membership integer")
    return value


def _fingerprint(value: object) -> str:
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise MembershipStateError("invalid card fingerprint")
    return value


def _validate_card(card: VerifiedCard) -> None:
    if type(card) is not VerifiedCard:
        raise MembershipStateError("verified card input required")
    _text(card.card_uid)
    _fingerprint(card.payload_fingerprint)
    if card.duration_seconds is not None:
        _integer(card.duration_seconds, minimum=1)
    if type(card.device_permission) is not str or card.device_permission not in {"bound", "roaming"}:
        raise MembershipStateError("invalid card permission")
    if type(card.legacy_use_state) is not str or card.legacy_use_state not in {"unused", "activated", "unknown"}:
        raise MembershipStateError("explicit validated card usage required")


def _validate_binding(binding: BindingObservation) -> None:
    if type(binding) is not BindingObservation:
        raise MembershipStateError("complete binding observation required")
    _text(binding.binding_reference)
    if binding.rebinds_used is not None:
        _integer(binding.rebinds_used)  # No invented/deployed maximum here.
    # None preserves the deployed v5 legacy string binding's unrecorded count.
    _text(binding.policy_reference)


def _validate_policy(policy: PurchaseGrantPolicy) -> None:
    if type(policy) is not PurchaseGrantPolicy:
        raise MembershipStateError("explicit purchase policy required")
    if (
        type(policy.policy_id) is not str
        or policy.policy_id != SELECTED_PURCHASE_POLICY.policy_id
        or type(policy.pet_30_seconds) is not int or policy.pet_30_seconds != 43_200
        or type(policy.carry_unused_pet_quota) is not bool or policy.carry_unused_pet_quota is not True
        or type(policy.roaming_extra_rebinds) is not int or policy.roaming_extra_rebinds != 0
    ):
        raise MembershipStateError("unsupported purchase policy")


def _validate_segments(segments: tuple[BenefitSegment, ...], start: int, end: int) -> None:
    if type(segments) is not tuple or not segments:
        raise MembershipStateError("complete benefit segments required")
    cursor = start
    for segment in segments:
        if type(segment) is not BenefitSegment or type(segment.plan_kind) is not str or segment.plan_kind not in _LEVEL:
            raise MembershipStateError("invalid benefit segment")
        _integer(segment.starts_at, minimum=1)
        _integer(segment.ends_at, minimum=1)
        _fingerprint(segment.source_fingerprint)
        if segment.starts_at != cursor or segment.ends_at <= cursor or segment.ends_at > end:
            raise MembershipStateError("noncanonical benefit coverage")
        cursor = segment.ends_at
    if cursor != end:
        raise MembershipStateError("incomplete benefit coverage")


def _validate_seed(seed: ValidatedMembershipSeed, now: int) -> None:
    if type(seed) is not ValidatedMembershipSeed or seed.validation_complete is not True:
        raise MembershipStateError("complete validated seed required")
    _text(seed.account_uid)
    _validate_card(seed.original_card)
    _validate_binding(seed.binding_observation)
    _integer(seed.started_at, minimum=1)
    _integer(seed.validated_at, minimum=1)
    _text(seed.pet_ledger_reference)
    _integer(seed.pet_quota_total_seconds)
    if seed.pet_quota_used_seconds is not None:
        _integer(seed.pet_quota_used_seconds, maximum=seed.pet_quota_total_seconds)
    if seed.validated_at > now or seed.started_at > seed.validated_at or seed.account_uid != seed.original_card.card_uid:
        raise MembershipStateError("inconsistent validated seed")
    if seed.membership_exp is None:
        if seed.original_card.duration_seconds is not None or seed.benefit_segments != ():
            raise MembershipStateError("inconsistent permanent seed")
        return
    _integer(seed.membership_exp, minimum=1)
    if seed.original_card.duration_seconds is None or seed.membership_exp != seed.started_at + seed.original_card.duration_seconds:
        raise MembershipStateError("inconsistent legacy clock")
    if seed.original_card.plan_kind is None:
        if seed.benefit_segments != ():
            raise MembershipStateError("unsupported legacy segments")
        return  # Valid credential shape; ordinary custom activation belongs to adapter.
    _validate_segments(seed.benefit_segments, seed.started_at, seed.membership_exp)
    if len(seed.benefit_segments) != 1 or seed.benefit_segments[0] != BenefitSegment(
        seed.original_card.plan_kind, seed.started_at, seed.membership_exp, seed.original_card.payload_fingerprint
    ):
        raise MembershipStateError("inconsistent legacy plan source")
    if seed.pet_quota_total_seconds != (43_200 if seed.original_card.plan_kind == "30d" else 0):
        raise MembershipStateError("inconsistent legacy quota source")


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    out = {}
    for key, value in pairs:
        if key in out:
            raise MembershipStateError("duplicate membership field")
        out[key] = value
    return out


def _decode(raw: object) -> dict:
    if type(raw) is not str:
        raise MembershipStateError("invalid membership document")
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(MembershipStateError("invalid number")))
    except (TypeError, ValueError, RecursionError):
        raise MembershipStateError("invalid membership document") from None
    if type(value) is not dict:
        raise MembershipStateError("invalid membership document")
    return value


def _keys(value: object, keys: set[str]) -> dict:
    if type(value) is not dict or set(value) != keys:
        raise MembershipStateError("invalid membership fields")
    return value


def _encode(value: dict) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _schema_identity(connection: sqlite3.Connection) -> tuple:
    return tuple((kind, name, table, " ".join(sql.split())) for kind, name, table, sql in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ))


def _expected_schema() -> tuple:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(_SCHEMA)
        return _schema_identity(connection)


_EXPECTED_SCHEMA = _expected_schema()


def _validate_database(connection: sqlite3.Connection) -> None:
    if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
        or _schema_identity(connection) != _EXPECTED_SCHEMA
        or connection.execute("PRAGMA quick_check(1)").fetchone() != ("ok",)
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
        raise MembershipStateError("membership database contract mismatch")


def _seed_document(seed: ValidatedMembershipSeed) -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "account_uid": seed.account_uid,
        "revision": 1, "started_at": seed.started_at, "membership_exp": seed.membership_exp,
        "device_permission": seed.original_card.device_permission,
        "original_card": asdict(seed.original_card),
        "benefit_segments": [asdict(s) for s in seed.benefit_segments],
        "binding_observation": asdict(seed.binding_observation),
        "pet_quota_total_seconds": seed.pet_quota_total_seconds,
        "pet_quota_used_seconds": seed.pet_quota_used_seconds,
        "pet_ledger_reference": seed.pet_ledger_reference,
        "purchase_grants": [],
    }


def _document_parts(doc: dict) -> tuple[VerifiedCard, tuple[BenefitSegment, ...], BindingObservation, tuple[PurchaseGrant, ...]]:
    _keys(doc, {"schema_version", "account_uid", "revision", "started_at", "membership_exp", "device_permission",
                "original_card", "benefit_segments", "binding_observation", "pet_quota_total_seconds",
                "pet_quota_used_seconds", "pet_ledger_reference", "purchase_grants"})
    if type(doc["schema_version"]) is not int or doc["schema_version"] != SCHEMA_VERSION:
        raise MembershipStateError("invalid membership schema")
    _text(doc["account_uid"])
    _integer(doc["revision"], minimum=1)
    _integer(doc["started_at"], minimum=1)
    _integer(doc["membership_exp"], minimum=1)
    _integer(doc["pet_quota_total_seconds"])
    if doc["pet_quota_used_seconds"] is not None:
        _integer(doc["pet_quota_used_seconds"], maximum=doc["pet_quota_total_seconds"])
    _text(doc["pet_ledger_reference"])
    card = VerifiedCard(**_keys(doc["original_card"], set(VerifiedCard.__dataclass_fields__)))
    _validate_card(card)
    if card.card_uid != doc["account_uid"] or card.device_permission != doc["device_permission"] or card.plan_kind is None:
        raise MembershipStateError("invalid membership origin")
    binding = BindingObservation(**_keys(doc["binding_observation"], set(BindingObservation.__dataclass_fields__)))
    _validate_binding(binding)
    if type(doc["benefit_segments"]) is not list or type(doc["purchase_grants"]) is not list:
        raise MembershipStateError("invalid membership collections")
    segments = tuple(BenefitSegment(**_keys(s, set(BenefitSegment.__dataclass_fields__))) for s in doc["benefit_segments"])
    _validate_segments(segments, doc["started_at"], doc["membership_exp"])
    grants = tuple(PurchaseGrant(**_keys(g, set(PurchaseGrant.__dataclass_fields__))) for g in doc["purchase_grants"])
    seen = {card.payload_fingerprint}
    source_limits = {card.payload_fingerprint: (card.plan_kind, doc["started_at"], doc["started_at"] + card.duration_seconds)}
    cumulative_exp = doc["started_at"] + card.duration_seconds
    previous_time = doc["started_at"]
    for grant in grants:
        _fingerprint(grant.grant_id)
        _fingerprint(grant.source_fingerprint)
        _integer(grant.granted_at, minimum=1)
        _integer(grant.purchased_seconds, minimum=1)
        if grant.source_fingerprint in seen or grant.grant_id != _grant_id(grant.source_fingerprint):
            raise MembershipStateError("invalid purchase identity")
        seen.add(grant.source_fingerprint)
        if (type(grant.plan_kind) is not str or grant.plan_kind not in _LEVEL
            or grant.purchased_seconds != _LEVEL[grant.plan_kind] * DAY_SECONDS
            or type(grant.pet_quota_added_seconds) is not int
            or grant.pet_quota_added_seconds != (43_200 if grant.plan_kind == "30d" else 0)
            or grant.carry_unused_pet_quota is not True
            or type(grant.roaming_rebinds_added) is not int or grant.roaming_rebinds_added != 0
            or type(grant.policy_id) is not str or grant.policy_id != SELECTED_PURCHASE_POLICY.policy_id):
            raise MembershipStateError("invalid purchase grant")
        if grant.granted_at < previous_time or grant.granted_at >= cumulative_exp:
            raise MembershipStateError("invalid purchase clock")
        previous_time = grant.granted_at
        cumulative_exp += grant.purchased_seconds
        source_limits[grant.source_fingerprint] = (grant.plan_kind, grant.granted_at, cumulative_exp)
    if any(segment.source_fingerprint not in seen for segment in segments):
        raise MembershipStateError("unknown benefit source")
    for segment in segments:
        source_plan, source_start, source_end = source_limits[segment.source_fingerprint]
        if segment.plan_kind != source_plan or segment.starts_at < source_start or segment.ends_at > source_end:
            raise MembershipStateError("invalid benefit purchase authority")
    # Individually plausible source windows are insufficient: delaying a real
    # upgrade or prematurely starting a lower purchased tier can fit them.
    # Replay the same canonical transition used by the writer, in grant order.
    replay_exp = doc["started_at"] + card.duration_seconds
    replay_segments = (BenefitSegment(card.plan_kind, doc["started_at"], replay_exp, card.payload_fingerprint),)
    for grant in grants:
        next_exp = replay_exp + grant.purchased_seconds
        replay_segments = _extend_segments(
            replay_segments, now=grant.granted_at, old_exp=replay_exp, new_exp=next_exp,
            plan_kind=grant.plan_kind, source_fingerprint=grant.source_fingerprint,
        )
        replay_exp = next_exp
    if segments != replay_segments:
        raise MembershipStateError("benefit purchase replay mismatch")
    if doc["membership_exp"] != doc["started_at"] + card.duration_seconds + sum(g.purchased_seconds for g in grants):
        raise MembershipStateError("invalid accumulated clock")
    if doc["revision"] != 1 + len(grants):
        raise MembershipStateError("invalid membership revision")
    if doc["pet_quota_total_seconds"] != (43_200 if card.plan_kind == "30d" else 0):
        raise MembershipStateError("invalid legacy quota source")
    return card, segments, binding, grants


def _grant_id(fingerprint: str) -> str:
    return hashlib.sha256(("membership-purchase-v1|" + fingerprint).encode("ascii")).hexdigest()


def _project(doc: dict, now: int) -> MembershipSnapshot:
    _, segments, binding, grants = _document_parts(doc)
    active = next((s for s in segments if s.starts_at <= now < s.ends_at), None)

    def ends_for(minimum: int, *, exact: bool = False) -> int | None:
        if active is None:
            return None
        eligible = _LEVEL[active.plan_kind] == minimum if exact else _LEVEL[active.plan_kind] >= minimum
        if not eligible:
            return None
        end = active.ends_at
        for s in segments:
            continues = _LEVEL[s.plan_kind] == minimum if exact else _LEVEL[s.plan_kind] >= minimum
            if s.starts_at == end and continues:
                end = s.ends_at
        return end

    plan_exp = None
    if active is not None:
        plan_exp = active.ends_at
        for segment in segments:
            if segment.starts_at == plan_exp and segment.plan_kind == active.plan_kind:
                plan_exp = segment.ends_at
    return MembershipSnapshot(
        doc["account_uid"], doc["revision"], doc["membership_exp"], doc["device_permission"],
        active.plan_kind if active else None, plan_exp, ends_for(90), ends_for(365, exact=True),
        ends_for(90), segments, grants, binding,
        doc["pet_quota_total_seconds"] + sum(g.pet_quota_added_seconds for g in grants),
        doc["pet_quota_used_seconds"], doc["pet_ledger_reference"],
    )


def _extend_segments(old: tuple[BenefitSegment, ...], *, now: int, old_exp: int,
                     new_exp: int, plan_kind: str, source_fingerprint: str) -> tuple[BenefitSegment, ...]:
    # Pointwise max preserves every old higher deadline; a new higher purchase
    # upgrades all remaining and appended time immediately, not only the tail.
    points = sorted({old[0].starts_at, now, old_exp, new_exp, *(s.starts_at for s in old), *(s.ends_at for s in old)})
    out: list[BenefitSegment] = []
    for start, end in zip(points, points[1:]):
        previous = next((s for s in old if s.starts_at <= start < s.ends_at), None)
        if start < now:
            assert previous is not None
            source, plan = previous.source_fingerprint, previous.plan_kind
        elif previous is not None and _LEVEL[previous.plan_kind] > _LEVEL[plan_kind]:
            source, plan = previous.source_fingerprint, previous.plan_kind
        else:
            source, plan = source_fingerprint, plan_kind
        segment = BenefitSegment(plan, start, end, source)
        if out and out[-1].plan_kind == plan and out[-1].source_fingerprint == source:
            last = out.pop()
            segment = BenefitSegment(plan, last.starts_at, end, source)
        out.append(segment)
    return tuple(out)


class MembershipCardStore:
    """Explicitly initialized SQLite authority, with cross-process write locking."""
    def __init__(self, path: Path, *, timeout_seconds: float = 5.0,
                 fault_hook: Callable[[str], None] | None = None) -> None:
        if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
            raise MembershipStateError("membership database unavailable")
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
            raise MembershipStateError("invalid transaction timeout")
        self.path = path.resolve()
        self.timeout_seconds = float(timeout_seconds)
        self._fault_hook = fault_hook  # Test-only failure seam, never serialized.
        with self._connection() as connection:
            _validate_database(connection)

    @staticmethod
    def initialize(path: Path) -> None:
        """Create new empty authority only; never replace/migrate an existing file."""
        if not isinstance(path, Path):
            raise MembershipStateError("invalid database path")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n" + _SCHEMA
                    + f"PRAGMA application_id={APPLICATION_ID}; PRAGMA user_version={SCHEMA_VERSION}; COMMIT;"
                )
                _validate_database(connection)
        except (OSError, sqlite3.Error):
            raise MembershipStateError("membership initialization failed") from None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                         timeout=self.timeout_seconds, isolation_level=None)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        except sqlite3.Error:
            raise MembershipStateError("membership database unavailable") from None
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[tuple[sqlite3.Connection, list[bool]]]:
        with self._connection() as connection:
            committed = False
            changed = [False]
            try:
                connection.execute("BEGIN IMMEDIATE")
                _validate_database(connection)
                yield connection, changed
                if changed[0]:
                    if self._fault_hook is not None:
                        self._fault_hook("before_commit")
                    connection.execute("COMMIT")
                    committed = True
                    if self._fault_hook is not None:
                        self._fault_hook("after_commit")
                else:
                    connection.execute("ROLLBACK")
            except Exception as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                if committed:
                    raise MembershipCommitUnknownError("membership commit needs confirmation") from None
                if isinstance(exc, MembershipStateError):
                    raise
                raise MembershipStateError("membership transaction failed") from None

    @staticmethod
    def _load(connection: sqlite3.Connection, account_uid: str) -> dict | None:
        row = connection.execute("SELECT revision,document FROM accounts WHERE account_uid=?", (account_uid,)).fetchone()
        if row is None:
            return None
        doc = _decode(row[1])
        _document_parts(doc)
        if doc["account_uid"] != account_uid or doc["revision"] != row[0]:
            raise MembershipStateError("membership row mismatch")
        original, _, _, grants = _document_parts(doc)
        claims = connection.execute(
            "SELECT card_uid,payload_fingerprint,account_uid,operation,receipt FROM card_claims WHERE account_uid=?",
            (account_uid,),
        ).fetchall()
        if len(claims) != 1 + len(grants):
            raise MembershipStateError("incomplete membership claims")
        by_fingerprint = {r[1]: MembershipCardStore._receipt(r) for r in claims}
        cumulative_exp = doc["started_at"] + original.duration_seconds
        origin = by_fingerprint.get(original.payload_fingerprint)
        if (origin is None or origin.card_uid != original.card_uid or origin.operation != "first_activation"
            or origin.revision != 1 or origin.membership_exp != cumulative_exp):
            raise MembershipStateError("invalid membership origin claim")
        for revision, grant in enumerate(grants, start=2):
            cumulative_exp += grant.purchased_seconds
            receipt = by_fingerprint.get(grant.source_fingerprint)
            if (receipt is None or receipt.operation != "redeem" or receipt.revision != revision
                or receipt.membership_exp != cumulative_exp or receipt.credited_seconds != grant.purchased_seconds
                or receipt.grant_id != grant.grant_id or receipt.pet_quota_added_seconds != grant.pet_quota_added_seconds):
                raise MembershipStateError("invalid membership purchase claim")
        return doc

    @staticmethod
    def _claims(connection: sqlite3.Connection, card: VerifiedCard) -> list[tuple]:
        return connection.execute(
            "SELECT card_uid,payload_fingerprint,account_uid,operation,receipt FROM card_claims "
            "WHERE card_uid=? OR payload_fingerprint=?", (card.card_uid, card.payload_fingerprint)
        ).fetchall()

    @staticmethod
    def _receipt(row: tuple) -> MembershipReceipt:
        raw = _decode(row[4])
        receipt = MembershipReceipt(**_keys(raw, set(MembershipReceipt.__dataclass_fields__)))
        _fingerprint(receipt.receipt_id)
        _fingerprint(receipt.payload_fingerprint)
        _integer(receipt.revision, minimum=1)
        _integer(receipt.membership_exp, minimum=1)
        _integer(receipt.credited_seconds)
        _integer(receipt.pet_quota_added_seconds)
        _integer(receipt.roaming_rebinds_added)
        _text(receipt.card_uid)
        _text(receipt.account_uid)
        if receipt.receipt_id != hashlib.sha256(("membership-receipt-v1|" + receipt.operation + "|" + receipt.payload_fingerprint).encode("ascii")).hexdigest():
            raise MembershipStateError("invalid receipt identity")
        if (receipt.card_uid, receipt.payload_fingerprint, receipt.account_uid, receipt.operation) != row[:4]:
            raise MembershipStateError("card receipt mismatch")
        if receipt.operation not in {"first_activation", "redeem"}:
            raise MembershipStateError("invalid receipt operation")
        if receipt.operation == "first_activation":
            if receipt.credited_seconds != 0 or receipt.grant_id is not None or receipt.pet_quota_added_seconds != 0 or receipt.roaming_rebinds_added != 0:
                raise MembershipStateError("invalid initial receipt")
        elif (receipt.grant_id != _grant_id(receipt.payload_fingerprint) or receipt.roaming_rebinds_added != 0
              or receipt.credited_seconds not in {d * DAY_SECONDS for d in _PLANS}
              or receipt.pet_quota_added_seconds != (43_200 if receipt.credited_seconds == 30 * DAY_SECONDS else 0)):
            raise MembershipStateError("invalid renewal receipt")
        return receipt

    @staticmethod
    def _save(connection: sqlite3.Connection, doc: dict) -> None:
        _document_parts(doc)
        connection.execute(
            "INSERT INTO accounts(account_uid,revision,document) VALUES(?,?,?) "
            "ON CONFLICT(account_uid) DO UPDATE SET revision=excluded.revision,document=excluded.document",
            (doc["account_uid"], doc["revision"], _encode(doc)),
        )

    @staticmethod
    def _claim(connection: sqlite3.Connection, card: VerifiedCard, doc: dict, operation: str,
               *, grant: PurchaseGrant | None = None) -> MembershipReceipt:
        receipt = MembershipReceipt(
            hashlib.sha256(("membership-receipt-v1|" + operation + "|" + card.payload_fingerprint).encode("ascii")).hexdigest(),
            doc["account_uid"], card.card_uid, card.payload_fingerprint, operation, doc["revision"],
            doc["membership_exp"], grant.purchased_seconds if grant else 0, grant.grant_id if grant else None,
            grant.pet_quota_added_seconds if grant else 0, 0,
        )
        connection.execute("INSERT INTO card_claims VALUES(?,?,?,?,?)", (
            card.card_uid, card.payload_fingerprint, doc["account_uid"], operation, _encode(asdict(receipt)),
        ))
        return receipt

    def read(self, account_uid: str, *, now: int) -> MembershipSnapshot | None:
        _text(account_uid)
        _integer(now, minimum=1)
        with self._transaction() as (connection, _):
            doc = self._load(connection, account_uid)
            return _project(doc, now) if doc else None

    def lookup_claim(self, card: VerifiedCard) -> MembershipReceipt | None:
        """Privileged adapter recovery/alias lookup; never expose owner in an error."""
        _validate_card(card)
        with self._transaction() as (connection, _):
            rows = self._claims(connection, card)
            if not rows:
                return None
            if len(rows) != 1 or rows[0][:2] != (card.card_uid, card.payload_fingerprint):
                raise MembershipStateError("card identity collision")
            receipt = self._receipt(rows[0])
            doc = self._load(connection, receipt.account_uid)
            if doc is None:
                raise MembershipStateError("card owner unavailable")
            return receipt

    def claims_for_account(self, account_uid: str) -> tuple[MembershipReceipt, ...]:
        """Server-only source-revocation inventory, with complete lineage proof.

        Returns no token/payload. Raw card UID fields are privileged adapter
        inputs; do not serialize this method as a public endpoint or log it.
        """
        _text(account_uid)
        with self._transaction() as (connection, _):
            if self._load(connection, account_uid) is None:
                return ()
            rows = connection.execute(
                "SELECT card_uid,payload_fingerprint,account_uid,operation,receipt FROM card_claims WHERE account_uid=?",
                (account_uid,),
            ).fetchall()
            return tuple(sorted((self._receipt(row) for row in rows), key=lambda receipt: receipt.revision))

    def claim_for_card_uid(self, card_uid: str) -> MembershipReceipt | None:
        """Server-only revocation projection, not proof a card may be redeemed.

        Exact named-card lookup; validate its entire owner's lineage. Never
        serialize raw receipt/UID fields as a public endpoint or log inventory.
        """
        _text(card_uid)
        with self._transaction() as (connection, _):
            row = connection.execute(
                "SELECT card_uid,payload_fingerprint,account_uid,operation,receipt FROM card_claims WHERE card_uid=?",
                (card_uid,),
            ).fetchone()
            if row is None:
                return None
            receipt = self._receipt(row)
            if self._load(connection, receipt.account_uid) is None:
                raise MembershipStateError("card owner unavailable")
            return receipt

    def record_first_activation(self, seed: ValidatedMembershipSeed, *, now: int) -> OperationResult:
        """Claim a finite canonical first use in the SAME gate as redemption.

        The adapter supplies the fully validated start/expiry/binding/Pet seed;
        it must coordinate any legacy side effects. This method does not write
        bindings.json or claim that a cross-file legacy transaction is atomic.
        """
        _integer(now, minimum=1)
        _validate_seed(seed, now)
        card = seed.original_card
        if seed.membership_exp is None:
            return OperationResult(OperationStatus.SWITCH_PERMANENT)
        if card.plan_kind is None:
            return OperationResult(OperationStatus.UNSUPPORTED_PLAN)
        if seed.membership_exp <= now:
            return OperationResult(OperationStatus.UNSUPPORTED_EXPIRED)
        if card.legacy_use_state == "unknown":
            return OperationResult(OperationStatus.UNSUPPORTED_USAGE)
        with self._transaction() as (connection, changed):
            rows = self._claims(connection, card)
            if rows:
                if len(rows) == 1 and rows[0][:2] == (card.card_uid, card.payload_fingerprint) and rows[0][2] == seed.account_uid:
                    receipt = self._receipt(rows[0])
                    doc = self._load(connection, seed.account_uid)
                    if doc is None:
                        raise MembershipStateError("card owner unavailable")
                    return OperationResult(OperationStatus.REPLAYED, receipt, _project(doc, now))
                return OperationResult(OperationStatus.CARD_UNAVAILABLE)
            if self._load(connection, seed.account_uid) is not None:
                return OperationResult(OperationStatus.UNSUPPORTED_IDENTITY)
            doc = _seed_document(seed)
            self._save(connection, doc)
            receipt = self._claim(connection, card, doc, "first_activation")
            changed[0] = True
            return OperationResult(OperationStatus.FIRST_ACTIVATED, receipt, _project(doc, now))

    def redeem(self, account_uid: str, card: VerifiedCard, *, now: int,
               policy: PurchaseGrantPolicy, legacy_seed: ValidatedMembershipSeed | None = None) -> OperationResult:
        _text(account_uid)
        _integer(now, minimum=1)
        _validate_card(card)
        _validate_policy(policy)
        if legacy_seed is not None:
            _validate_seed(legacy_seed, now)
            if legacy_seed.account_uid != account_uid:
                raise MembershipStateError("seed target mismatch")
        with self._transaction() as (connection, changed):
            doc = self._load(connection, account_uid)
            rows = self._claims(connection, card)
            if rows:
                if len(rows) != 1 or rows[0][:2] != (card.card_uid, card.payload_fingerprint):
                    return OperationResult(OperationStatus.UNSUPPORTED_IDENTITY)
                receipt = self._receipt(rows[0])
                owner_doc = self._load(connection, receipt.account_uid)
                if owner_doc is None:
                    raise MembershipStateError("card owner unavailable")
                if receipt.operation == "first_activation":
                    return OperationResult(OperationStatus.SWITCH_USED_CARD, snapshot=_project(doc, now) if doc else None)
                if receipt.account_uid != account_uid:
                    return OperationResult(OperationStatus.CARD_UNAVAILABLE)
                if doc is None:
                    raise MembershipStateError("card owner unavailable")
                return OperationResult(OperationStatus.REPLAYED, receipt, _project(doc, now))
            if doc is None:
                if legacy_seed is None:
                    raise MembershipStateError("validated legacy seed required")
                if legacy_seed.membership_exp is None:
                    return OperationResult(OperationStatus.SWITCH_PERMANENT)
                if legacy_seed.original_card.plan_kind is None:
                    return OperationResult(OperationStatus.UNSUPPORTED_PLAN)
                if legacy_seed.original_card.card_uid == card.card_uid:
                    return OperationResult(OperationStatus.SWITCH_USED_CARD if legacy_seed.original_card.payload_fingerprint == card.payload_fingerprint else OperationStatus.UNSUPPORTED_IDENTITY)
                if legacy_seed.original_card.payload_fingerprint == card.payload_fingerprint:
                    return OperationResult(OperationStatus.UNSUPPORTED_IDENTITY)
                doc = _seed_document(legacy_seed)
            snapshot = _project(doc, now)
            _, _, _, prior_grants = _document_parts(doc)
            if now < (prior_grants[-1].granted_at if prior_grants else doc["started_at"]):
                raise MembershipStateError("membership clock moved backwards")
            if snapshot.membership_exp <= now:
                return OperationResult(OperationStatus.UNSUPPORTED_EXPIRED, snapshot=snapshot)
            # Absence in this NEW store is not evidence that a legacy card was
            # unused. The adapter must qualify actual paid_starts/binding state.
            if card.legacy_use_state == "activated":
                return OperationResult(OperationStatus.SWITCH_USED_CARD, snapshot=snapshot)
            if card.legacy_use_state != "unused":
                return OperationResult(OperationStatus.UNSUPPORTED_USAGE, snapshot=snapshot)
            if card.duration_seconds is None:
                return OperationResult(OperationStatus.SWITCH_PERMANENT, snapshot=snapshot)
            if card.plan_kind is None:
                return OperationResult(OperationStatus.UNSUPPORTED_PLAN, snapshot=snapshot)
            if card.device_permission != snapshot.device_permission:
                return OperationResult(OperationStatus.SWITCH_PERMISSION, snapshot=snapshot)
            new_exp = snapshot.membership_exp + card.duration_seconds
            if new_exp > MAX_SAFE_EPOCH:
                return OperationResult(OperationStatus.UNSUPPORTED_OVERFLOW, snapshot=snapshot)
            if not connection.execute("SELECT 1 FROM accounts WHERE account_uid=?", (account_uid,)).fetchone():
                original = legacy_seed.original_card
                if self._claims(connection, original):
                    raise MembershipStateError("legacy origin already claimed")
                self._save(connection, doc)
                self._claim(connection, original, doc, "first_activation")
            grant = PurchaseGrant(_grant_id(card.payload_fingerprint), card.payload_fingerprint, card.plan_kind,
                                  card.duration_seconds, now, policy.pet_30_seconds if card.plan_kind == "30d" else 0,
                                  policy.carry_unused_pet_quota, policy.roaming_extra_rebinds, policy.policy_id)
            doc["membership_exp"] = new_exp
            doc["revision"] += 1
            doc["benefit_segments"] = [asdict(s) for s in _extend_segments(
                snapshot.benefit_segments, now=now, old_exp=snapshot.membership_exp, new_exp=new_exp,
                plan_kind=card.plan_kind, source_fingerprint=card.payload_fingerprint)]
            doc["purchase_grants"].append(asdict(grant))
            self._save(connection, doc)
            receipt = self._claim(connection, card, doc, "redeem", grant=grant)
            changed[0] = True
            return OperationResult(OperationStatus.REDEEMED, receipt, _project(doc, now))
