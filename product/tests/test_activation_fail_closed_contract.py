# -*- coding: utf-8 -*-
"""Activation security-state and client-cache fail-closed contract."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from bidking_lab.secure import activation, client_activate as ca

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import activation_core as ac  # noqa: E402
import activation_server as asv  # noqa: E402


DEVICE = "DEVICE_FAIL_CLOSED"
TOKEN = "v1|paid-user|1000.signature"
NOW = 1_800_000_000
EXP = NOW + 86_400
MASTER_KEY = bytes(range(32))
MASTER_KEY_B64 = base64.b64encode(MASTER_KEY).decode("ascii")
SECRET = b"activation-fail-closed-test-secret"
INGEST = "https://api.example/ingest"


@pytest.fixture(autouse=True)
def _isolate_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for key in (
        "BIDKING_ACTIVATION_TOKEN",
        "BIDKING_ACTIVATION_FILE",
        "BIDKING_ACTIVATE_URL",
        "BIDKING_INTEGRITY_DIGEST",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ca, "device_fingerprint", lambda: DEVICE)
    monkeypatch.setattr(ca, "_package_dir", lambda: tmp_path / "_package")
    ca._CACHE_INVALIDATED_IN_PROCESS = False
    yield
    ca._CACHE_INVALIDATED_IN_PROCESS = False
    activation.set_provider(activation._DevStubProvider())


def _lease(
    *,
    exp=EXP,
    paid_exp=ca._MISSING,
    device=DEVICE,
    uid="paid-user",
    plan_kind=ca._MISSING,
    feature_entitlements=ca._MISSING,
) -> dict:
    value = {"uid": uid, "device": device, "exp": exp}
    if paid_exp is not ca._MISSING:
        value["paid_exp"] = paid_exp
    if plan_kind is not ca._MISSING:
        value["plan_kind"] = plan_kind
    if feature_entitlements is not ca._MISSING:
        value["feature_entitlements"] = feature_entitlements
    return value


def _signed_lease(value: dict) -> str:
    return json.dumps(value, separators=(",", ":")) + ".signature"


def _ok_body(*, mode="paid", lease=None, master_key=MASTER_KEY_B64, trial_exp=ca._MISSING) -> dict:
    body = {
        "ok": True,
        "mode": mode,
        "lease": _signed_lease(lease or _lease()),
        "master_key": master_key,
    }
    if trial_exp is not ca._MISSING:
        body["trial_exp"] = trial_exp
    return body


def _fake_response(body: dict, *, code: int = 200):
    def fake(_url, _device, _token, _timeout):
        return code, body

    return fake


@pytest.mark.parametrize(
    "bad",
    [True, False, "1800086400", 1.5, None, [], {}, float("nan"), float("inf"), 10**100, 0, -1],
)
def test_client_rejects_non_exact_or_unbounded_lease_exp(bad):
    assert ca._validated_activation_fields(
        lease=_lease(exp=bad),
        master_key_b64=MASTER_KEY_B64,
        mode="paid",
        device=DEVICE,
        now=NOW,
    ) is None


@pytest.mark.parametrize(
    "bad",
    [None, "", "not-base64", base64.b64encode(b"").decode(),
     base64.b64encode(b"x" * 16).decode(), base64.b64encode(b"x" * 31).decode(),
     base64.b64encode(b"x" * 33).decode()],
)
def test_client_requires_exact_32_byte_master_key(bad):
    assert ca._validated_activation_fields(
        lease=_lease(),
        master_key_b64=bad,
        mode="paid",
        device=DEVICE,
        now=NOW,
    ) is None


@pytest.mark.parametrize("bad", [None, "", "paid ", "TRIAL", 1, True, [], {}])
def test_client_requires_exact_mode(bad):
    assert ca._validated_activation_fields(
        lease=_lease(),
        master_key_b64=MASTER_KEY_B64,
        mode=bad,
        device=DEVICE,
        now=NOW,
    ) is None


@pytest.mark.parametrize("bad_uid", [None, "", 1, True, [], {}])
def test_client_requires_nonempty_string_uid(bad_uid):
    assert ca._validated_activation_fields(
        lease=_lease(uid=bad_uid),
        master_key_b64=MASTER_KEY_B64,
        mode="paid",
        device=DEVICE,
        now=NOW,
    ) is None


def test_client_requires_current_device_and_allows_legacy_permanent_paid_lease():
    assert ca._validated_activation_fields(
        lease=_lease(device="OTHER_DEVICE"),
        master_key_b64=MASTER_KEY_B64,
        mode="paid",
        device=DEVICE,
        now=NOW,
    ) is None
    fields = ca._validated_activation_fields(
        lease=_lease(),
        master_key_b64=MASTER_KEY_B64,
        mode="paid",
        device=DEVICE,
        now=NOW,
    )
    assert fields is not None and fields["paid_exp"] is None


@pytest.mark.parametrize(
    "bad",
    [True, "1800086400", 1.5, None, [], {}, float("nan"), float("inf"), 10**100, 0, -1, EXP - 1],
)
def test_client_rejects_malformed_or_short_paid_exp(bad):
    assert ca._validated_activation_fields(
        lease=_lease(paid_exp=bad),
        master_key_b64=MASTER_KEY_B64,
        mode="paid",
        device=DEVICE,
        now=NOW,
    ) is None


@pytest.mark.parametrize("trial_exp", [ca._MISSING, True, "1800086400", EXP + 1, None])
def test_client_trial_exp_is_required_exact_and_matches_lease(trial_exp):
    assert ca._validated_activation_fields(
        lease=_lease(uid=f"trial:{DEVICE}"),
        master_key_b64=MASTER_KEY_B64,
        mode="trial",
        device=DEVICE,
        now=NOW,
        trial_exp=trial_exp,
    ) is None
    valid = ca._validated_activation_fields(
        lease=_lease(uid=f"trial:{DEVICE}"),
        master_key_b64=MASTER_KEY_B64,
        mode="trial",
        device=DEVICE,
        now=NOW,
        trial_exp=EXP,
    )
    assert valid is not None and valid["trial_exp"] == EXP


def test_valid_finite_paid_response_is_accepted_and_cached(
    monkeypatch: pytest.MonkeyPatch,
):
    paid_exp = EXP + 3600
    monkeypatch.setattr(
        ca,
        "_post_activate",
        _fake_response(_ok_body(lease=_lease(paid_exp=paid_exp))),
    )

    result = ca.activate(
        ui_token=TOKEN,
        ingest_url=INGEST,
        now=NOW,
        force_server=True,
    )

    assert result.activated and result.source == "server"
    assert result.mode == "paid" and result.paid_exp == paid_exp
    assert len(result.master_key or b"") == 32
    assert ca._load_cache(device=DEVICE, token=TOKEN) is not None


def test_signed_365_entitlement_is_online_only_and_cache_cannot_authorize(
    monkeypatch: pytest.MonkeyPatch,
):
    paid_exp = EXP + 365 * 86400
    lease = _lease(
        paid_exp=paid_exp,
        plan_kind="365d",
        feature_entitlements={"pet_companion": "full"},
    )
    monkeypatch.setattr(ca, "_post_activate", _fake_response(_ok_body(lease=lease)))

    online = ca.activate(
        ui_token=TOKEN,
        ingest_url=INGEST,
        now=NOW,
        force_server=True,
    )
    cached = ca._result_from_cache(
        ca._load_cache(device=DEVICE, token=TOKEN),
        device=DEVICE,
        ingest_url=INGEST,
        now=NOW,
    )

    assert online.activated and online.source == "server"
    assert cached is None
    assert online.plan_kind == "365d"
    assert online.feature_entitlements == {
        "pet_companion": "full",
    }
    assert online.paid_exp == paid_exp


def test_locally_forged_365_cache_never_activates_when_https_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    assert ca.save_token(TOKEN)
    assert ca._save_cache(
        lease=_lease(
            paid_exp=EXP + 365 * 86400,
            plan_kind="365d",
            feature_entitlements={"pet_companion": "full"},
        ),
        master_key=MASTER_KEY,
        device=DEVICE,
        token=TOKEN,
        mode="paid",
        now=NOW,
    )
    monkeypatch.setattr(ca, "_post_activate", lambda *_args: None)
    monkeypatch.setattr(
        ca,
        "_install_provider",
        lambda *_args, **_kwargs: pytest.fail("disk cache must not install provider"),
    )

    result = ca.activate(ingest_url=INGEST, now=NOW, force_server=True)

    assert not result.activated
    assert result.reason == "offline" and result.source == "server"
    assert ca._result_from_cache(
        ca._load_cache(device=DEVICE, token=TOKEN),
        device=DEVICE,
        ingest_url=INGEST,
        now=NOW,
    ) is None


def test_old_finite_cache_refreshes_entitlement_during_startup(
    monkeypatch: pytest.MonkeyPatch,
):
    ca.save_token(TOKEN)
    assert ca._save_cache(
        lease=_lease(paid_exp=EXP + 365 * 86400),
        master_key=MASTER_KEY,
        device=DEVICE,
        token=TOKEN,
        mode="paid",
        now=NOW,
    )
    calls = {"count": 0}
    refreshed_lease = _lease(
        paid_exp=EXP + 365 * 86400,
        plan_kind="365d",
        feature_entitlements={"pet_companion": "full"},
    )

    def respond_with_entitlement(*_args):
        calls["count"] += 1
        return 200, _ok_body(lease=refreshed_lease)

    monkeypatch.setattr(ca, "_post_activate", respond_with_entitlement)

    result = ca.activate(ingest_url=INGEST, now=NOW)

    assert calls["count"] == 1
    assert result.source == "server" and result.plan_kind == "365d"
    assert result.feature_entitlements == {"pet_companion": "full"}


@pytest.mark.parametrize(
    ("mode", "plan_kind", "pet_access", "paid_exp", "trial_exp"),
    [
        ("paid", "30d", "none", EXP + 30 * 86400, ca._MISSING),
        ("paid", "90d", "none", EXP + 90 * 86400, ca._MISSING),
        ("paid", "365d", "full", EXP + 365 * 86400, ca._MISSING),
        ("paid", "legacy_permanent", "full", ca._MISSING, ca._MISSING),
        ("trial", "trial", "none", ca._MISSING, EXP),
    ],
)
def test_client_accepts_complete_plan_entitlement_matrix(
    mode: str,
    plan_kind: str,
    pet_access: str,
    paid_exp,
    trial_exp,
):
    lease = _lease(
        uid=(f"trial:{DEVICE}" if mode == "trial" else "paid-user"),
        paid_exp=paid_exp,
        plan_kind=plan_kind,
        feature_entitlements={"pet_companion": pet_access},
    )
    fields = ca._validated_activation_fields(
        lease=lease,
        master_key_b64=MASTER_KEY_B64,
        mode=mode,
        device=DEVICE,
        now=NOW,
        trial_exp=trial_exp,
    )

    assert fields is not None
    assert fields["plan_kind"] == plan_kind
    assert fields["feature_entitlements"] == {
        "pet_companion": pet_access,
    }


@pytest.mark.parametrize(
    "case",
    [
        "lease_missing",
        "lease_none",
        "lease_bad_signature_payload",
        "exp_missing",
        "exp_bool",
        "exp_string",
        "exp_float",
        "exp_list",
        "exp_nan",
        "exp_inf",
        "exp_negative",
        "exp_huge",
        "master_key_missing",
        "master_key_invalid_base64",
        "master_key_empty",
        "master_key_1",
        "master_key_16",
        "master_key_31",
        "master_key_33",
        "mode_missing",
        "mode_list",
        "mode_unknown",
        "device_missing",
        "device_other",
        "uid_missing",
        "uid_list",
        "trial_exp_missing",
        "trial_exp_none",
        "trial_exp_string",
        "trial_exp_list",
        "trial_exp_nan",
        "trial_exp_inf",
        "trial_exp_mismatch",
        "paid_exp_bool",
        "paid_exp_string",
        "paid_exp_list",
        "paid_exp_dict",
        "paid_exp_nan",
        "paid_exp_inf",
        "paid_exp_negative",
        "paid_exp_before_lease",
        "entitlement_plan_only",
        "entitlement_features_only",
        "entitlement_unknown_plan",
        "entitlement_features_list",
        "entitlement_missing_pet",
        "entitlement_invalid_pet",
        "entitlement_365_denied",
        "entitlement_30_full",
        "entitlement_finite_permanent",
        "entitlement_paid_trial_mode",
    ],
)
def test_malicious_ok_response_is_offline_and_never_cached(
    case: str, monkeypatch: pytest.MonkeyPatch
):
    mode = "paid"
    lease = _lease()
    key = MASTER_KEY_B64
    trial_exp = ca._MISSING
    if case == "exp_missing":
        lease.pop("exp")
    elif case == "exp_bool":
        lease["exp"] = True
    elif case == "exp_string":
        lease["exp"] = str(EXP)
    elif case == "exp_float":
        lease["exp"] = float(EXP)
    elif case == "exp_list":
        lease["exp"] = [EXP]
    elif case == "exp_nan":
        lease["exp"] = float("nan")
    elif case == "exp_inf":
        lease["exp"] = float("inf")
    elif case == "exp_negative":
        lease["exp"] = -1
    elif case == "exp_huge":
        lease["exp"] = 10**100
    elif case == "master_key_invalid_base64":
        key = "not-base64"
    elif case == "master_key_empty":
        key = ""
    elif case.startswith("master_key_") and case != "master_key_missing":
        key = base64.b64encode(b"x" * int(case.rsplit("_", 1)[1])).decode()
    elif case == "mode_list":
        mode = []
    elif case == "mode_unknown":
        mode = "paid "
    elif case == "device_missing":
        lease.pop("device")
    elif case == "device_other":
        lease["device"] = "OTHER_DEVICE"
    elif case == "uid_missing":
        lease.pop("uid")
    elif case == "uid_list":
        lease["uid"] = ["paid-user"]
    elif case.startswith("trial_exp_"):
        mode = "trial"
        lease["uid"] = f"trial:{DEVICE}"
        trial_exp = {
            "trial_exp_missing": ca._MISSING,
            "trial_exp_none": None,
            "trial_exp_string": str(EXP),
            "trial_exp_list": [EXP],
            "trial_exp_nan": float("nan"),
            "trial_exp_inf": float("inf"),
            "trial_exp_mismatch": EXP + 1,
        }[case]
    elif case.startswith("paid_exp_"):
        lease["paid_exp"] = {
            "paid_exp_bool": True,
            "paid_exp_string": str(EXP),
            "paid_exp_list": [EXP],
            "paid_exp_dict": {"exp": EXP},
            "paid_exp_nan": float("nan"),
            "paid_exp_inf": float("inf"),
            "paid_exp_negative": -1,
            "paid_exp_before_lease": EXP - 1,
        }[case]
    elif case == "entitlement_plan_only":
        lease["plan_kind"] = "365d"
    elif case == "entitlement_features_only":
        lease["feature_entitlements"] = {"pet_companion": "full"}
    elif case == "entitlement_unknown_plan":
        lease["plan_kind"] = "vip"
        lease["feature_entitlements"] = {"pet_companion": "full"}
    elif case == "entitlement_features_list":
        lease["plan_kind"] = "365d"
        lease["feature_entitlements"] = ["pet_companion", "full"]
    elif case == "entitlement_missing_pet":
        lease["plan_kind"] = "365d"
        lease["feature_entitlements"] = {}
    elif case == "entitlement_invalid_pet":
        lease["plan_kind"] = "365d"
        lease["feature_entitlements"] = {"pet_companion": True}
    elif case == "entitlement_365_denied":
        lease["plan_kind"] = "365d"
        lease["feature_entitlements"] = {"pet_companion": "none"}
    elif case == "entitlement_30_full":
        lease["plan_kind"] = "30d"
        lease["feature_entitlements"] = {"pet_companion": "full"}
    elif case == "entitlement_finite_permanent":
        lease["paid_exp"] = EXP + 3600
        lease["plan_kind"] = "legacy_permanent"
        lease["feature_entitlements"] = {"pet_companion": "full"}
    elif case == "entitlement_paid_trial_mode":
        lease["plan_kind"] = "trial"
        lease["feature_entitlements"] = {"pet_companion": "none"}
    body = _ok_body(mode=mode, lease=lease, master_key=key, trial_exp=trial_exp)
    if case == "lease_missing":
        body.pop("lease")
    elif case == "lease_none":
        body["lease"] = None
    elif case == "lease_bad_signature_payload":
        body["lease"] = "not-json.signature"
    elif case == "master_key_missing":
        body.pop("master_key")
    elif case == "mode_missing":
        body.pop("mode")
    monkeypatch.setattr(ca, "_post_activate", _fake_response(body))

    result = ca.activate(ui_token=TOKEN, ingest_url=INGEST, now=NOW, force_server=True)

    assert not result.activated and result.reason == "offline" and result.source == "server"
    assert not ca._cache_file().exists()
    assert ca._read_persisted_token() == ""


@pytest.mark.parametrize(
    "case",
    ["missing", "none", "false_string", "true_string", "zero", "one", "list", "dict"],
)
def test_client_requires_exact_boolean_ok(case: str, monkeypatch: pytest.MonkeyPatch):
    body = _ok_body()
    if case == "missing":
        body.pop("ok")
    else:
        body["ok"] = {
            "none": None,
            "false_string": "false",
            "true_string": "true",
            "zero": 0,
            "one": 1,
            "list": [],
            "dict": {},
        }[case]
    monkeypatch.setattr(ca, "_post_activate", _fake_response(body))

    result = ca.activate(ui_token=TOKEN, ingest_url=INGEST, now=NOW, force_server=True)

    assert not result.activated and result.reason == "offline"
    assert not ca._cache_file().exists()
    assert ca._read_persisted_token() == ""


def test_non_boolean_ok_preserves_cache_bytes_but_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    ca.save_token(TOKEN)
    assert ca._save_cache(
        lease=_lease(paid_exp=EXP + 3600), master_key=MASTER_KEY,
        device=DEVICE, token=TOKEN, mode="paid", now=NOW,
    )
    original = ca._cache_file().read_bytes()
    body = _ok_body(lease=_lease(paid_exp=EXP + 3600))
    body["ok"] = "false"
    monkeypatch.setattr(ca, "_post_activate", _fake_response(body))

    result = ca.activate(ingest_url=INGEST, now=NOW, force_server=True)

    assert not result.activated and result.source == "server"
    assert result.reason == "offline"
    assert ca._cache_file().read_bytes() == original


def test_cache_is_bound_to_exact_token_and_device():
    assert ca._save_cache(
        lease=_lease(), master_key=MASTER_KEY, device=DEVICE,
        token=TOKEN, mode="paid", now=NOW,
    )
    assert ca._load_cache(device=DEVICE, token=TOKEN) is not None
    assert ca._load_cache(device=DEVICE, token="v1|other|1000.signature") is None
    assert ca._load_cache(device="OTHER_DEVICE", token=TOKEN) is None


def test_malformed_ok_response_preserves_cache_without_authorizing(
    monkeypatch: pytest.MonkeyPatch,
):
    ca.save_token(TOKEN)
    assert ca._save_cache(
        lease=_lease(paid_exp=EXP + 3600), master_key=MASTER_KEY,
        device=DEVICE, token=TOKEN, mode="paid", now=NOW,
    )
    original = ca._cache_file().read_bytes()
    bad = _ok_body(lease=_lease(paid_exp=str(EXP + 3600)))
    monkeypatch.setattr(ca, "_post_activate", _fake_response(bad))

    result = ca.activate(ingest_url=INGEST, now=NOW, force_server=True)

    assert not result.activated and result.source == "server"
    assert result.reason == "offline"
    assert ca._cache_file().read_bytes() == original


@pytest.mark.parametrize("deny_reason", ["revoked", "device_mismatch"])
def test_authoritative_deny_with_unlink_failure_tombstones_cache_across_restart(
    monkeypatch: pytest.MonkeyPatch,
    deny_reason: str,
):
    ca.save_token(TOKEN)
    assert ca._save_cache(
        lease=_lease(), master_key=MASTER_KEY, device=DEVICE,
        token=TOKEN, mode="paid", now=NOW,
    )
    cache_path = ca._cache_file()
    real_unlink = Path.unlink

    def fail_cache_unlink(self: Path, *args, **kwargs):
        if self == cache_path:
            raise PermissionError("synthetic cache lock")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_cache_unlink)
    monkeypatch.setattr(
        ca,
        "_post_activate",
        _fake_response({"ok": False, "mode": "paid", "reason": deny_reason}, code=403),
    )

    result = ca.activate(ingest_url=INGEST, now=NOW, force_server=True)

    assert not result.activated and result.reason == deny_reason
    assert cache_path.exists() and ca._cache_invalid_file().exists()
    ca._CACHE_INVALIDATED_IN_PROCESS = False  # simulate a fresh process
    assert ca._load_cache(device=DEVICE, token=TOKEN) is None


def test_manual_clear_reports_cache_failure_and_marker_result(
    monkeypatch: pytest.MonkeyPatch,
):
    ca.save_token(TOKEN)
    ca._cache_file().parent.mkdir(parents=True, exist_ok=True)
    ca._cache_file().write_bytes(b"old-cache")
    cache_path = ca._cache_file()
    real_unlink = Path.unlink

    def fail_cache_unlink(self: Path, *args, **kwargs):
        if self == cache_path:
            raise PermissionError("synthetic cache lock")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_cache_unlink)
    result = ca.clear_activation()
    assert result == {
        "cleared": False,
        "token_cleared": True,
        "cache_cleared": False,
        "cache_invalidated": True,
        "env_was_set": False,
    }


def test_manual_clear_reports_token_file_failure_truthfully(
    monkeypatch: pytest.MonkeyPatch,
):
    ca.save_token(TOKEN)
    token_path = ca._token_file()
    real_unlink = Path.unlink

    def fail_token_unlink(self: Path, *args, **kwargs):
        if self == token_path:
            raise PermissionError("synthetic token lock")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_token_unlink)

    result = ca.clear_activation()

    assert not result["cleared"]
    assert not result["token_cleared"]
    assert result["cache_cleared"]
    assert token_path.exists()


def test_valid_online_response_atomically_replaces_tombstoned_cache(
    monkeypatch: pytest.MonkeyPatch,
):
    ca.save_token(TOKEN)
    ca._cache_file().parent.mkdir(parents=True, exist_ok=True)
    ca._cache_file().write_bytes(b"stale")
    assert ca._write_cache_tombstone()
    ca._CACHE_INVALIDATED_IN_PROCESS = False  # simulate restart with persisted marker
    monkeypatch.setattr(ca, "_post_activate", _fake_response(_ok_body()))

    result = ca.activate(ingest_url=INGEST, now=NOW, force_server=True)

    assert result.activated and result.source == "server"
    assert not ca._cache_invalid_file().exists()
    assert ca._load_cache(device=DEVICE, token=TOKEN) is not None


def test_cache_write_failure_does_not_clear_existing_tombstone(
    monkeypatch: pytest.MonkeyPatch,
):
    ca.save_token(TOKEN)
    assert ca._write_cache_tombstone()
    ca._CACHE_INVALIDATED_IN_PROCESS = False
    real_atomic = ca._atomic_write_bytes

    def fail_cache_write(path: Path, data: bytes):
        if path == ca._cache_file():
            raise OSError("synthetic cache write failure")
        return real_atomic(path, data)

    monkeypatch.setattr(ca, "_atomic_write_bytes", fail_cache_write)
    monkeypatch.setattr(ca, "_post_activate", _fake_response(_ok_body()))

    result = ca.activate(ingest_url=INGEST, now=NOW, force_server=True)

    assert not result.activated and result.reason == "offline"
    assert ca._cache_invalid_file().exists()


def test_cache_and_tombstone_write_failure_still_fails_closed_in_process(
    monkeypatch: pytest.MonkeyPatch,
):
    ca._cache_file().parent.mkdir(parents=True, exist_ok=True)
    ca._cache_file().write_bytes(b"stale")
    cache_path = ca._cache_file()
    real_unlink = Path.unlink

    def fail_cache_unlink(self: Path, *args, **kwargs):
        if self == cache_path:
            raise PermissionError("synthetic cache lock")
        return real_unlink(self, *args, **kwargs)

    def fail_marker_write(_path: Path, _data: bytes):
        raise OSError("synthetic marker write failure")

    monkeypatch.setattr(Path, "unlink", fail_cache_unlink)
    monkeypatch.setattr(ca, "_atomic_write_bytes", fail_marker_write)
    result = ca.clear_activation()
    assert not result["cleared"]
    assert not result["cache_cleared"]
    assert not result["cache_invalidated"]
    assert ca._CACHE_INVALIDATED_IN_PROCESS
    assert ca._load_cache(device=DEVICE, token=TOKEN) is None


def _service(tmp_path: Path) -> asv.ActivationService:
    return asv.ActivationService(
        secret=SECRET,
        master_key_b64=MASTER_KEY_B64,
        bindings_path=tmp_path / "bindings.json",
        revoked_path=tmp_path / "revoked.txt",
        telemetry_path=tmp_path / "telemetry.jsonl",
        trials_path=tmp_path / "trials.json",
        paid_starts_path=tmp_path / "paid_starts.json",
        trial_hours=12.0,
    )


def _paid_token(uid="paid-user") -> str:
    return ac.mint_token(uid, SECRET, iat=NOW, exp=NOW + 30 * 86_400)


def test_missing_security_state_is_valid_first_run_and_persists_fixed_trial_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(ac.time, "time", lambda: NOW)
    service = _service(tmp_path)

    paid_code, paid_body = service.handle_activate(
        {"token": _paid_token(), "device": DEVICE}
    )
    trial_code, trial_body = service.handle_activate(
        {"device": "FIRST_TRIAL_DEVICE"}
    )

    assert paid_code == 200 and paid_body["ok"] and paid_body["mode"] == "paid"
    assert trial_code == 200 and trial_body["ok"] and trial_body["mode"] == "trial"
    assert json.loads((tmp_path / "paid_starts.json").read_text(encoding="utf-8")) == {
        "paid-user": NOW,
    }
    assert json.loads((tmp_path / "trials.json").read_text(encoding="utf-8")) == {
        "FIRST_TRIAL_DEVICE": {
            "schema_version": 2,
            "start": NOW,
            "window_seconds": 12 * 3600,
            "last_server_now": NOW,
        },
    }


@pytest.mark.parametrize("bad", [None, "", False, 0, [], {}])
def test_server_rejects_invalid_target_binding_without_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad
):
    monkeypatch.setattr(ac.time, "time", lambda: NOW)
    path = tmp_path / "bindings.json"
    original = json.dumps({"paid-user": bad})
    path.write_text(original, encoding="utf-8")
    service = _service(tmp_path)

    code, body = service.handle_activate({"token": _paid_token(), "device": "NEW_DEVICE"})

    assert code == 503
    assert body == {"ok": False, "mode": "paid", "reason": "state_error"}
    assert "lease" not in body and "master_key" not in body
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("state_name", "content", "request_body", "mode"),
    [
        ("bindings.json", {"other": None}, {"token": "paid", "device": DEVICE}, "paid"),
        ("paid_starts.json", {"other": "bad"}, {"token": "paid", "device": DEVICE}, "paid"),
        ("trials.json", {"other": None}, {"device": DEVICE}, "trial"),
        ("trials.json", {"other": {"used": 10}}, {"device": DEVICE}, "trial"),
    ],
)
def test_server_rejects_invalid_unrelated_state_entry_without_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_name: str,
    content: dict,
    request_body: dict,
    mode: str,
):
    monkeypatch.setattr(ac.time, "time", lambda: NOW)
    path = tmp_path / state_name
    original = json.dumps(content)
    path.write_text(original, encoding="utf-8")
    service = _service(tmp_path)
    actual_request = dict(request_body)
    if actual_request.get("token") == "paid":
        actual_request["token"] = _paid_token()

    code, body = service.handle_activate(actual_request)

    assert code == 503
    assert body == {"ok": False, "mode": mode, "reason": "state_error"}
    assert "lease" not in body and "master_key" not in body
    assert path.read_text(encoding="utf-8") == original


def test_unreadable_revocation_file_fails_closed_for_activation_and_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    revoked_path = tmp_path / "revoked.txt"
    revoked_path.write_text("paid-user\n", encoding="utf-8")
    service = _service(tmp_path)
    real_read_text = Path.read_text

    def fail_revoked_read(self: Path, *args, **kwargs):
        if self == revoked_path:
            raise PermissionError("synthetic revoked state read failure")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_revoked_read)

    code, body = service.handle_activate({"token": _paid_token(), "device": DEVICE})
    assert code == 503
    assert body == {"ok": False, "mode": "paid", "reason": "state_error"}
    assert "lease" not in body and "master_key" not in body

    annual = ac.mint_token("annual", SECRET, iat=NOW, exp=NOW + 365 * 86_400)
    ok, reason, entitlement = service.authorize_private_dashboard(annual, now=NOW)
    assert not ok and reason == "state_error" and entitlement is None


@pytest.mark.parametrize(
    "bad",
    [True, False, "1800000000", 1.5, None, [], {}, float("nan"), float("inf"), 10**100, 0, -1, NOW + 301],
)
def test_server_rejects_invalid_paid_start_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad
):
    monkeypatch.setattr(ac.time, "time", lambda: NOW)
    path = tmp_path / "paid_starts.json"
    original = json.dumps({"paid-user": bad})
    path.write_text(original, encoding="utf-8")
    service = _service(tmp_path)

    code, body = service.handle_activate({"token": _paid_token(), "device": DEVICE})

    assert code == 503
    assert body == {"ok": False, "mode": "paid", "reason": "state_error"}
    assert "lease" not in body and "master_key" not in body
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("content", ["{", "[]", '"text"', "null"])
@pytest.mark.parametrize("state_name", ["bindings.json", "paid_starts.json", "trials.json"])
def test_existing_malformed_or_nonobject_security_state_returns_503_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_name: str, content: str
):
    monkeypatch.setattr(ac.time, "time", lambda: NOW)
    path = tmp_path / state_name
    path.write_text(content, encoding="utf-8")
    service = _service(tmp_path)
    request = ({"device": DEVICE} if state_name == "trials.json"
               else {"token": _paid_token(), "device": DEVICE})

    code, body = service.handle_activate(request)

    expected_mode = "trial" if state_name == "trials.json" else "paid"
    assert code == 503
    assert body == {"ok": False, "mode": expected_mode, "reason": "state_error"}
    assert path.read_text(encoding="utf-8") == content


def test_unreadable_existing_security_state_returns_structured_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "paid_starts.json"
    path.write_text("{}", encoding="utf-8")
    service = _service(tmp_path)
    real_read = Path.read_text

    def fail_target_read(self: Path, *args, **kwargs):
        if self == path:
            raise OSError("synthetic state read failure")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_target_read)
    code, body = service.handle_activate({"token": _paid_token(), "device": DEVICE})
    assert code == 503
    assert body == {"ok": False, "mode": "paid", "reason": "state_error"}


@pytest.mark.parametrize("bad", [True, "1800000000", 1.5, None, [], {}, 0, -1, NOW + 301])
def test_private_dashboard_uses_same_strict_paid_clock(
    tmp_path: Path, bad
):
    path = tmp_path / "paid_starts.json"
    original = json.dumps({"annual": bad})
    path.write_text(original, encoding="utf-8")
    service = _service(tmp_path)
    token = ac.mint_token("annual", SECRET, iat=NOW, exp=NOW + 365 * 86_400)

    ok, reason, entitlement = service.authorize_private_dashboard(token, now=NOW)

    assert not ok and reason == "state_error" and entitlement is None
    assert path.read_text(encoding="utf-8") == original


def test_legacy_trial_usage_dict_migrates_once_without_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(ac.time, "time", lambda: NOW)
    path = tmp_path / "trials.json"
    path.write_text(json.dumps({DEVICE: {"used": 120, "last": NOW - 10}}), encoding="utf-8")
    service = _service(tmp_path)

    code, body = service.handle_activate({"device": DEVICE})

    assert code == 200 and body["ok"] and body["mode"] == "trial"
    assert json.loads(path.read_text(encoding="utf-8"))[DEVICE] == {
        "schema_version": 2,
        "start": NOW - 120,
        "window_seconds": 12 * 3600,
        "last_server_now": NOW,
    }
