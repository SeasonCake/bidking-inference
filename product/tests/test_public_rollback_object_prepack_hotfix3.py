from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_public_rollback_object.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rollback_object_prepack", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _small_seed(path: Path) -> bytes:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("one.txt", b"one")
        archive.writestr("two.txt", b"two")
    return path.read_bytes()


def _pin_seed(module, monkeypatch, path: Path, *, members: int) -> bytes:
    raw = path.read_bytes()
    monkeypatch.setattr(module, "SEED_BYTES", len(raw))
    monkeypatch.setattr(module, "SEED_SHA256", _sha(raw))
    monkeypatch.setattr(module, "SEED_MEMBER_COUNT", members)
    return raw


def _unix_member(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    if name.endswith("/"):
        info.external_attr |= 0x10
    return info


def _fixture(tmp_path: Path, monkeypatch):
    module = _load_module()
    seed_path = tmp_path / "seed.zip"
    seed_raw = _small_seed(seed_path)
    monkeypatch.setattr(module, "SEED_BYTES", len(seed_raw))
    monkeypatch.setattr(module, "SEED_SHA256", _sha(seed_raw))
    monkeypatch.setattr(module, "SEED_MEMBER_COUNT", 2)
    current = {
        "latest": "0.3.2-hotfix2",
        "min": "0.3.1",
        "url": "https://objects.example.test/current.zip",
        "notice": "current",
        "game_version": "",
    }
    current_raw = _canonical(current)
    envelope = {
        "schema_version": 1,
        "operation_id": "hotfix5-rollback-test-v1",
        "observed_at": "2026-08-11T01:02:03Z",
        "public_base": "https://objects.example.test",
        "object_prefix": "rollback/archive",
        "expected_current_manifest_sha256": _sha(current_raw),
        "rollback_manifest": {
            "latest": "0.3.1-hotfix5",
            "min": "0.3.1",
            "notice": "回滚测试通知",
            "game_version": "",
        },
    }
    input_path = tmp_path / "input.json"
    input_path.write_bytes(_canonical(envelope))
    output_dir = tmp_path / "output"
    args = argparse.Namespace(seed=seed_path, input=input_path, output_dir=output_dir)
    summary = module._prepare(args)
    plan_path = output_dir / "hotfix5.immutable-object-plan.json"
    candidate_path = output_dir / "version_manifest.hotfix5-rollback.candidate.json"
    receipt_path = output_dir / "hotfix5.offline-prepack-receipt.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    return {
        "module": module,
        "seed_path": seed_path,
        "seed_raw": seed_raw,
        "input_path": input_path,
        "envelope": envelope,
        "output_dir": output_dir,
        "summary": summary,
        "plan_path": plan_path,
        "plan": plan,
        "candidate_path": candidate_path,
        "receipt_path": receipt_path,
        "current": current,
        "current_raw": current_raw,
    }


class _Response:
    def __init__(self, *, status: int, url: str, headers: dict[str, str], body: bytes):
        self.status = status
        self._url = url
        self.headers = headers
        self._body = body
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self._offset
        result = self._body[self._offset : self._offset + size]
        self._offset += len(result)
        return result


def _valid_opener(plan: dict[str, object], seed_raw: bytes, requests: list):
    url = plan["immutable_object"]["url"]

    def open_one(request, timeout):
        requests.append((request, timeout))
        if request.get_method() == "HEAD":
            return _Response(
                status=200,
                url=url,
                headers={"Content-Length": str(len(seed_raw))},
                body=b"",
            )
        if request.get_header("Range") == "bytes=0-0":
            return _Response(
                status=206,
                url=url,
                headers={
                    "Content-Length": "1",
                    "Content-Range": f"bytes 0-0/{len(seed_raw)}",
                },
                body=seed_raw[:1],
            )
        return _Response(
            status=200,
            url=url,
            headers={"Content-Length": str(len(seed_raw))},
            body=seed_raw,
        )

    return open_one


def _object_receipt(fixture: dict[str, object]) -> dict[str, object]:
    requests: list = []
    return fixture["module"].verify_public_object(
        fixture["plan"],
        timeout=5.0,
        opener=_valid_opener(fixture["plan"], fixture["seed_raw"], requests),
    )


def test_template_has_exact_seed_pins_and_no_object_or_current_manifest_guess(capsys):
    module = _load_module()
    assert module.main(["template"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["public_base"] is None
    assert value["object_prefix"] is None
    assert value["expected_current_manifest_sha256"] is None
    assert value["rollback_manifest"]["notice"] is None
    assert value["seed_pins"]["sha256"] == module.SEED_SHA256


def test_prepare_validates_zip_and_derives_new_content_addressed_key(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    module = fixture["module"]
    plan = fixture["plan"]
    candidate_raw = fixture["candidate_path"].read_bytes()
    candidate = json.loads(candidate_raw)
    receipt_raw = fixture["receipt_path"].read_bytes()
    receipt = json.loads(receipt_raw)

    assert plan["seed"] == {
        "version": module.SEED_VERSION,
        "bytes": len(fixture["seed_raw"]),
        "sha256": _sha(fixture["seed_raw"]),
        "members": 2,
        "duplicates": 0,
        "crc": "pass",
        "range_0_0_sha256": _sha(fixture["seed_raw"][:1]),
    }
    assert plan["immutable_object"]["key"].endswith(
        f"-{_sha(fixture['seed_raw'])[:16]}.zip"
    )
    assert plan["immutable_object"]["url"] != module.HISTORICAL_URL
    assert candidate["url"] == plan["immutable_object"]["url"]
    assert candidate["latest"] == "0.3.1-hotfix5"
    assert _sha(candidate_raw) == plan["rollback_manifest_sha256"]
    assert plan["future_gates"] == {
        "historical_key_overwrite_forbidden": True,
        "anonymous_full_get_exact_readback_required": True,
        "root_backup_live_check_required": True,
    }
    assert all(plan["not_run"].values())
    assert receipt["status"] == "offline_prepack_verified"
    assert receipt["plan_sha256"] == _sha(fixture["plan_path"].read_bytes())
    assert receipt["rollback_manifest_sha256"] == _sha(candidate_raw)
    assert receipt["historical_public_object"]["lifecycle"] == (
        "historical_identity_public_unavailable"
    )
    assert receipt["future_gates"] == plan["future_gates"]
    assert receipt["secret_material_included"] is False
    assert receipt["production_bucket_credentials_included"] is False
    assert receipt["production_paths_included"] is False
    assert all(receipt["not_run"].values())
    assert fixture["summary"]["offline_prepack_receipt_sha256"] == _sha(receipt_raw)
    assert fixture["summary"]["upload_not_run"] is True


def test_prepare_refuses_existing_output_directory_and_preserves_bytes(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    original = {
        path.name: path.read_bytes()
        for path in fixture["output_dir"].iterdir()
    }
    args = argparse.Namespace(
        seed=fixture["seed_path"],
        input=fixture["input_path"],
        output_dir=fixture["output_dir"],
    )
    with pytest.raises(fixture["module"].RollbackPreparationError, match="output_directory_exists"):
        fixture["module"]._prepare(args)
    assert {
        path.name: path.read_bytes()
        for path in fixture["output_dir"].iterdir()
    } == original


def test_real_zip_crc_and_duplicate_gate_rejects_duplicate_member(tmp_path, monkeypatch):
    module = _load_module()
    seed = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(seed, "w") as archive:
            archive.writestr("same", b"one")
            archive.writestr("same", b"two")
    raw = seed.read_bytes()
    monkeypatch.setattr(module, "SEED_BYTES", len(raw))
    monkeypatch.setattr(module, "SEED_SHA256", _sha(raw))
    monkeypatch.setattr(module, "SEED_MEMBER_COUNT", 2)
    with pytest.raises(module.RollbackPreparationError, match="seed_duplicate_member"):
        module._verify_seed(seed)


def test_real_zip_crc_gate_rejects_corrupt_member(tmp_path, monkeypatch):
    module = _load_module()
    seed = tmp_path / "corrupt.zip"
    payload = b"unique-crc-payload"
    with zipfile.ZipFile(seed, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("safe.txt", payload)
    raw = seed.read_bytes()
    assert raw.count(payload) == 1
    seed.write_bytes(raw.replace(payload, b"X" + payload[1:]))
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_crc_mismatch"):
        module._verify_seed(seed)


def test_seed_stable_handle_gate_rejects_open_file_metadata_drift(tmp_path, monkeypatch):
    module = _load_module()
    seed = tmp_path / "drift.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr("safe.txt", b"safe")
    _pin_seed(module, monkeypatch, seed, members=1)
    real_fstat = module.os.fstat
    calls = 0

    def drifting_fstat(descriptor):
        nonlocal calls
        calls += 1
        value = real_fstat(descriptor)
        if calls != 2:
            return value
        return SimpleNamespace(
            st_mode=value.st_mode,
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns + 1,
            st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(module.os, "fstat", drifting_fstat)
    with pytest.raises(
        module.RollbackPreparationError,
        match="seed_changed_during_verification",
    ):
        module._verify_seed(seed)


def test_zip_member_path_gate_rejects_audit_traversal_known_fail(tmp_path, monkeypatch):
    module = _load_module()
    seed = tmp_path / "traversal.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr("../escape.txt", b"escape")
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_member_path_unsafe"):
        module._verify_seed(seed)


def test_zip_member_path_gate_keeps_safe_file_and_directory(tmp_path, monkeypatch):
    module = _load_module()
    seed = tmp_path / "safe.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr("pkg/", b"")
        archive.writestr("pkg/file.txt", b"safe")
    _pin_seed(module, monkeypatch, seed, members=2)

    result = module._verify_seed(seed)

    assert result["members"] == 2
    assert result["duplicates"] == 0
    assert result["crc"] == "pass"


@pytest.mark.parametrize(
    "name",
    [
        "COM\u00b9",
        "COM\u00b9.txt",
        "pkg/LPT\u00b2.log",
        "nested/deeper/com\u00b3/",
    ],
)
def test_zip_member_path_gate_rejects_windows_unicode_reserved_device_names(
    tmp_path,
    monkeypatch,
    name,
):
    module = _load_module()
    seed = tmp_path / "unicode-device.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(name, b"unsafe")
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_member_path_unsafe"):
        module._verify_seed(seed)


@pytest.mark.parametrize(
    "name",
    [
        "COM\u00b9x.txt",
        "LPT\u00b2-file",
        "pkg/com\u2074.txt",
        "nested/lpt\u00b3data/",
    ],
)
def test_zip_member_path_gate_keeps_unicode_device_name_lookalikes(
    tmp_path,
    monkeypatch,
    name,
):
    module = _load_module()
    seed = tmp_path / "unicode-lookalike.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(name, b"safe")
    _pin_seed(module, monkeypatch, seed, members=1)

    result = module._verify_seed(seed)

    assert result["members"] == 1
    assert result["crc"] == "pass"


@pytest.mark.parametrize(
    "name",
    [
        "/absolute.txt",
        "//server/share.txt",
        "C:/drive.txt",
        "C:drive-relative.txt",
        "pkg/./file.txt",
        "pkg/../file.txt",
        "pkg//file.txt",
        "pkg//",
        "pkg/file.txt.",
        "pkg/file.txt ",
        "pkg/CON.txt",
        "pkg/file:stream.txt",
        "pkg/file?.txt",
    ],
)
def test_zip_member_path_gate_rejects_noncanonical_or_windows_unsafe_names(
    tmp_path,
    monkeypatch,
    name,
):
    module = _load_module()
    seed = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(name, b"unsafe")
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_member_path_unsafe"):
        module._verify_seed(seed)


@pytest.mark.parametrize(
    ("original_name", "unsafe_name"),
    [
        (b"pkgXfile.txt", b"pkg\\file.txt"),
        (b"..Xescape.txt", b"..\\escape.txt"),
    ],
)
def test_zip_member_path_gate_rejects_raw_backslash_names(
    tmp_path,
    monkeypatch,
    original_name,
    unsafe_name,
):
    module = _load_module()
    seed = tmp_path / "backslash.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(original_name.decode("ascii"), b"unsafe")
    raw = seed.read_bytes()
    assert raw.count(original_name) == 2
    seed.write_bytes(raw.replace(original_name, unsafe_name))
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_member_path_unsafe"):
        module._verify_seed(seed)


def test_zip_member_path_gate_rejects_nul_name(tmp_path, monkeypatch):
    module = _load_module()
    seed = tmp_path / "nul.zip"
    original_name = b"nulXname.txt"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(original_name.decode("ascii"), b"unsafe")
    raw = seed.read_bytes()
    assert raw.count(original_name) == 2
    seed.write_bytes(raw.replace(original_name, b"nul\x00name.txt"))
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_member_path_unsafe"):
        module._verify_seed(seed)


@pytest.mark.parametrize(
    "names",
    [
        ("pkg/File.txt", "pkg/file.txt"),
        ("pkg/\u00e9.txt", "pkg/e\u0301.txt"),
        ("pkg", "pkg/child.txt"),
        ("pkg/child.txt", "PKG"),
    ],
)
def test_zip_member_path_gate_rejects_canonical_or_target_collisions(
    tmp_path,
    monkeypatch,
    names,
):
    module = _load_module()
    seed = tmp_path / "collision.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        for name in names:
            archive.writestr(name, b"collision")
    _pin_seed(module, monkeypatch, seed, members=len(names))

    with pytest.raises(module.RollbackPreparationError, match="seed_member_alias_collision"):
        module._verify_seed(seed)


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("link", stat.S_IFLNK | 0o777),
        ("pipe", stat.S_IFIFO | 0o600),
        ("device", stat.S_IFCHR | 0o600),
        ("regular-as-directory/", stat.S_IFREG | 0o600),
        ("directory-as-file", stat.S_IFDIR | 0o700),
    ],
)
def test_zip_member_type_gate_rejects_symlink_special_or_mismatch(
    tmp_path,
    monkeypatch,
    name,
    mode,
):
    module = _load_module()
    seed = tmp_path / "special.zip"
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(_unix_member(name, mode), b"")
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_member_type_unsafe"):
        module._verify_seed(seed)


def test_zip_member_type_gate_rejects_dos_volume_label(tmp_path, monkeypatch):
    module = _load_module()
    seed = tmp_path / "volume.zip"
    info = zipfile.ZipInfo("volume-label")
    info.create_system = 0
    info.external_attr = 0x08
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(info, b"")
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_member_type_unsafe"):
        module._verify_seed(seed)


def test_zip_member_type_gate_rejects_msdos_type_spoof(tmp_path, monkeypatch):
    module = _load_module()
    seed = tmp_path / "msdos-special.zip"
    info = zipfile.ZipInfo("spoofed-link")
    info.create_system = 0
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(info, b"")
    _pin_seed(module, monkeypatch, seed, members=1)

    with pytest.raises(module.RollbackPreparationError, match="seed_member_type_unsafe"):
        module._verify_seed(seed)


def test_zip_member_type_gate_keeps_msdos_directory_without_type_bits(
    tmp_path,
    monkeypatch,
):
    module = _load_module()
    seed = tmp_path / "msdos-directory.zip"
    info = zipfile.ZipInfo("data/")
    info.create_system = 0
    info.external_attr = 0o600 << 16
    with zipfile.ZipFile(seed, "w") as archive:
        archive.writestr(info, b"")
    _pin_seed(module, monkeypatch, seed, members=1)

    result = module._verify_seed(seed)

    assert result["members"] == 1
    assert result["crc"] == "pass"


def test_seed_byte_or_member_drift_is_fail_closed(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    module = fixture["module"]
    fixture["seed_path"].write_bytes(fixture["seed_raw"] + b"x")
    with pytest.raises(module.RollbackPreparationError, match="seed_identity_mismatch"):
        module._verify_seed(fixture["seed_path"])


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("public_base", "http://objects.example.test", "public_base_invalid"),
        ("public_base", "https://user:pass@objects.example.test", "public_base_invalid"),
        ("object_prefix", "../rollback", "object_prefix_invalid"),
        ("object_prefix", "rollback//archive", "object_prefix_invalid"),
        ("expected_current_manifest_sha256", "0" * 63, "current_manifest_sha256_invalid"),
    ],
)
def test_prepare_rejects_ambiguous_or_unsafe_operator_input(
    tmp_path,
    monkeypatch,
    field,
    value,
    reason,
):
    fixture = _fixture(tmp_path, monkeypatch)
    module = fixture["module"]
    envelope = fixture["envelope"]
    envelope[field] = value
    input_path = tmp_path / "bad-input.json"
    input_path.write_bytes(_canonical(envelope))
    args = argparse.Namespace(
        seed=fixture["seed_path"],
        input=input_path,
        output_dir=tmp_path / "bad-output",
    )
    with pytest.raises(module.RollbackPreparationError, match=reason):
        module._prepare(args)
    assert not args.output_dir.exists()


def test_anonymous_verifier_runs_head_range_and_full_get_without_credentials(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    requests: list = []
    receipt = fixture["module"].verify_public_object(
        fixture["plan"],
        timeout=5.0,
        opener=_valid_opener(fixture["plan"], fixture["seed_raw"], requests),
    )
    assert [request.get_method() for request, _timeout in requests] == ["HEAD", "GET", "GET"]
    assert requests[1][0].get_header("Range") == "bytes=0-0"
    for request, timeout in requests:
        lowered = {key.casefold() for key in request.headers}
        assert not lowered & {"authorization", "cookie", "proxy-authorization", "x-api-key"}
        assert timeout == 5.0
    assert receipt["status"] == "verified"
    assert receipt["anonymous_requests"] is True
    assert receipt["redirects_followed"] is False


@pytest.mark.parametrize("fault", ("redirect", "range_200", "range_wrong", "full_wrong"))
def test_object_verifier_rejects_redirect_range_and_full_get_faults(
    tmp_path,
    monkeypatch,
    fault,
):
    fixture = _fixture(tmp_path, monkeypatch)
    module = fixture["module"]
    plan = fixture["plan"]
    seed = fixture["seed_raw"]
    url = plan["immutable_object"]["url"]

    def opener(request, _timeout):
        if request.get_method() == "HEAD":
            return _Response(
                status=200,
                url="https://redirect.example.test/object" if fault == "redirect" else url,
                headers={"Content-Length": str(len(seed))},
                body=b"",
            )
        if request.get_header("Range"):
            return _Response(
                status=200 if fault == "range_200" else 206,
                url=url,
                headers={"Content-Range": f"bytes 0-0/{len(seed)}"},
                body=b"x" if fault == "range_wrong" else seed[:1],
            )
        body = seed + b"x" if fault == "full_wrong" else seed
        return _Response(
            status=200,
            url=url,
            headers={"Content-Length": str(len(body))},
            body=body,
        )

    with pytest.raises(module.RollbackPreparationError):
        module.verify_public_object(plan, timeout=5.0, opener=opener)


def test_manifest_preflight_binds_cas_object_receipt_and_absent_backup(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    module = fixture["module"]
    current_path = tmp_path / "version_manifest.json"
    current_path.write_bytes(fixture["current_raw"])
    receipt_path = tmp_path / "object-receipt.json"
    receipt_path.write_bytes(_canonical(_object_receipt(fixture)))
    backups = tmp_path / "backups"
    backups.mkdir()
    args = argparse.Namespace(
        plan=fixture["plan_path"],
        current_manifest=current_path,
        candidate_manifest=fixture["candidate_path"],
        object_receipt=receipt_path,
        backup_path=backups / "before.json",
        operation_dir=backups / "operation-v1",
    )
    result = module._manifest_preflight(args)
    assert result["status"] == "eligible_pending_exclusive_lock_and_apply"
    assert result["current_manifest_sha256"] == _sha(fixture["current_raw"])
    assert result["backup_path_absent"] is True
    assert result["operation_directory_absent"] is True
    assert result["apply_not_run"] is True
    assert not args.backup_path.exists()
    assert not args.operation_dir.exists()


def test_manifest_preflight_rejects_cas_drift_and_existing_backup(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    module = fixture["module"]
    current_path = tmp_path / "version_manifest.json"
    current_path.write_bytes(fixture["current_raw"] + b" ")
    receipt_path = tmp_path / "object-receipt.json"
    receipt_path.write_bytes(_canonical(_object_receipt(fixture)))
    backups = tmp_path / "backups"
    backups.mkdir()
    args = argparse.Namespace(
        plan=fixture["plan_path"],
        current_manifest=current_path,
        candidate_manifest=fixture["candidate_path"],
        object_receipt=receipt_path,
        backup_path=backups / "before.json",
        operation_dir=backups / "operation-v1",
    )
    with pytest.raises(module.RollbackPreparationError, match="current_manifest_cas_mismatch"):
        module._manifest_preflight(args)

    current_path.write_bytes(fixture["current_raw"])
    args.backup_path.write_bytes(b"existing")
    with pytest.raises(module.RollbackPreparationError, match="backup_path_exists_or_unsafe"):
        module._manifest_preflight(args)
    assert args.backup_path.read_bytes() == b"existing"


def test_object_receipt_unknown_secret_field_is_rejected(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    module = fixture["module"]
    receipt = _object_receipt(fixture)
    receipt["token"] = "must-not-pass"
    with pytest.raises(module.RollbackPreparationError, match="object_receipt_invalid"):
        module._object_receipt(receipt, fixture["plan"])


def test_receipt_write_is_exclusive_and_preserves_existing_bytes(tmp_path):
    module = _load_module()
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"existing")
    with pytest.raises(module.RollbackPreparationError, match="receipt_path_exists"):
        module._write_exclusive(receipt, b"replacement")
    assert receipt.read_bytes() == b"existing"
