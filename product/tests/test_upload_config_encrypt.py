# -*- coding: utf-8 -*-
"""Encrypted upload config contract (src/bidking_lab/live/sample_upload.py + secure/data_crypt.py).

Locks the 群发版 encrypted-upload-config moat (0.2.3-hotfix1):
  - build writes data/processed/upload_config.enc (BKENC1, AES-GCM with 出厂主密钥);
  - runtime _read_encrypted_upload_config decrypts via activation.data_key() (master key delivered
    post-activation, NOT in the package) -> 群友激活后 HMAC 上传, 提取包拿不到 key;
  - no enc / not activated / wrong key -> (None, "") -> no upload (opt-in preserved);
  - BIDKING_DISABLE_ENCRYPTED_UPLOAD_CONFIG falls back to plaintext (.local.ps1);
  - cache only on success -> late activation retried on next settlement.
Reviewed-in 2026-06-29."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from bidking_lab.live import sample_upload as su
from bidking_lab.secure import activation
from bidking_lab.secure.data_crypt import (
    DataKeyUnavailable,
    decrypt_bytes,
    encrypt_bytes,
    gen_key_b64,
    load_text,
)


_UPLOAD_CONFIG_MEMBER = "data/processed/upload_config.enc"


@pytest.fixture(autouse=True)
def _silence_upload_status(monkeypatch) -> None:
    # 别让测试往真实 repo 的 data/logs/live/upload_status.txt 落盘
    monkeypatch.setattr(su, "_write_upload_status", lambda *a, **k: None)


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    (tmp_path / "data" / "processed").mkdir(parents=True, exist_ok=True)
    return tmp_path


class _FakeProvider:
    """可控的 activation provider: data_key() 返回固定 key 或抛 DataKeyUnavailable。"""
    def __init__(self, key: bytes | None = None, raise_exc: type[Exception] | None = None):
        self._key = key
        self._raise = raise_exc

    def is_activated(self) -> bool:
        return self._key is not None and self._raise is None

    def data_key(self) -> bytes:
        if self._raise:
            raise self._raise("no key")
        assert self._key is not None
        return self._key

    def integrity_digest(self):  # noqa: D401
        return None

    def report_suspicious(self, verdict) -> None:  # noqa: D401
        return None


def _write_enc(repo: Path, cfg: dict, key: bytes) -> Path:
    enc = repo / "data" / "processed" / "upload_config.enc"
    enc.write_bytes(
        encrypt_bytes(
            json.dumps(cfg).encode("utf-8"),
            key,
            member_path=_UPLOAD_CONFIG_MEMBER,
        )
    )
    return enc


# ---- _read_encrypted_upload_config: 加密路径 ----

def test_encrypted_roundtrip(tmp_repo: Path, monkeypatch) -> None:
    key = base64.b64decode(gen_key_b64())
    monkeypatch.setattr(activation, "_provider", _FakeProvider(key=key))
    _write_enc(tmp_repo, {"url": "http://x.test:18900/ingest", "key": "ingest-k"}, key)
    url, ikey = su._read_encrypted_upload_config(tmp_repo)
    assert url == "http://x.test:18900/ingest"
    assert ikey == "ingest-k"


def test_bkenc_member_path_binding_rejects_swap_and_legacy_unbound(
    tmp_path: Path,
) -> None:
    key = base64.b64decode(gen_key_b64())
    member = "data/raw/tables/Item.txt"
    other = "data/raw/tables/Drop.txt"
    plain = b"member-bound moat"
    bound = encrypt_bytes(plain, key, member_path=member)

    assert decrypt_bytes(bound, key, member_path=member) == plain
    with pytest.raises(InvalidTag):
        decrypt_bytes(bound, key, member_path=other)

    legacy = encrypt_bytes(plain, key)
    with pytest.raises(InvalidTag):
        decrypt_bytes(legacy, key, member_path=member)

    runtime_path = tmp_path / Path(member)
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_bytes(bound)
    assert load_text(runtime_path, key, encoding="utf-8") == plain.decode()

    swapped_path = tmp_path / Path(other)
    swapped_path.write_bytes(bound)
    with pytest.raises(InvalidTag):
        load_text(swapped_path, key, encoding="utf-8")


def test_build_helper_roundtrips_written_blob_without_echoing_credentials(tmp_path: Path) -> None:
    key = base64.b64decode(gen_key_b64())
    local = tmp_path / "sample_upload.local.ps1"
    out = tmp_path / "upload_config.enc"
    url = "https://collector.example.invalid/ingest"
    ingest_key = "do-not-echo-this-key"
    local.write_text(
        f'$env:BIDKING_SAMPLE_UPLOAD_URL="{url}"\n'
        f'$env:BIDKING_SAMPLE_UPLOAD_KEY="{ingest_key}"\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["BIDKING_DATA_KEY"] = base64.b64encode(key).decode("ascii")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "encrypt_upload_config.py"),
            "--local",
            str(local),
            "--out",
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "roundtrip verified" in proc.stdout
    assert url not in proc.stdout
    assert ingest_key not in proc.stdout
    assert json.loads(
        decrypt_bytes(
            out.read_bytes(),
            key,
            member_path=_UPLOAD_CONFIG_MEMBER,
        )
    ) == {"url": url, "key": ingest_key}


def test_build_helper_rejects_plain_http_public_upload_config(tmp_path: Path) -> None:
    key = base64.b64decode(gen_key_b64())
    local = tmp_path / "sample_upload.local.ps1"
    out = tmp_path / "upload_config.enc"
    ingest_key = "do-not-echo-http-key"
    local.write_text(
        '$env:BIDKING_SAMPLE_UPLOAD_URL="http://203.0.113.10:18900/ingest"\n'
        f'$env:BIDKING_SAMPLE_UPLOAD_KEY="{ingest_key}"\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["BIDKING_DATA_KEY"] = base64.b64encode(key).decode("ascii")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "encrypt_upload_config.py"),
            "--local",
            str(local),
            "--out",
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert proc.returncode == 3
    assert "必须使用 HTTPS" in proc.stderr
    assert ingest_key not in proc.stdout + proc.stderr
    assert not out.exists()


def test_encrypted_no_file_returns_none(tmp_repo: Path, monkeypatch) -> None:
    key = base64.b64decode(gen_key_b64())
    monkeypatch.setattr(activation, "_provider", _FakeProvider(key=key))
    assert su._read_encrypted_upload_config(tmp_repo) == (None, "")


def test_encrypted_not_activated_returns_none(tmp_repo: Path, monkeypatch) -> None:
    key = base64.b64decode(gen_key_b64())
    _write_enc(tmp_repo, {"url": "http://x.test/ingest", "key": "k"}, key)
    monkeypatch.setattr(activation, "_provider", _FakeProvider(raise_exc=DataKeyUnavailable))
    assert su._read_encrypted_upload_config(tmp_repo) == (None, "")  # 未激活 -> 不上传


def test_encrypted_wrong_key_returns_none(tmp_repo: Path, monkeypatch) -> None:
    key = base64.b64decode(gen_key_b64())
    _write_enc(tmp_repo, {"url": "http://x.test/ingest", "key": "k"}, key)
    monkeypatch.setattr(activation, "_provider", _FakeProvider(key=b"0" * 32))  # 错 key
    assert su._read_encrypted_upload_config(tmp_repo) == (None, "")  # InvalidTag 被接住 -> 不上传


def test_encrypted_not_bkenc1_returns_none(tmp_repo: Path, monkeypatch) -> None:
    key = base64.b64decode(gen_key_b64())
    monkeypatch.setattr(activation, "_provider", _FakeProvider(key=key))
    (tmp_repo / "data" / "processed" / "upload_config.enc").write_bytes(b"not encrypted junk")
    assert su._read_encrypted_upload_config(tmp_repo) == (None, "")


# ---- _read_local_upload_config: 优先级 / 回退开关 / 缓存 ----

def test_read_local_picks_encrypted_when_available(monkeypatch) -> None:
    monkeypatch.setattr(su, "_LOCAL_CFG_CACHE", None)
    monkeypatch.setattr(su, "_read_encrypted_upload_config", lambda repo: ("http://enc/ingest", "ek"))
    monkeypatch.setattr(su, "_read_plaintext_upload_config", lambda repo: ("http://plain/ingest", "pk"))
    monkeypatch.delenv(su._DISABLE_ENC_CFG, raising=False)
    assert su._read_local_upload_config() == ("http://enc/ingest", "ek")


def test_read_local_falls_back_to_plaintext_when_no_enc(monkeypatch) -> None:
    monkeypatch.setattr(su, "_LOCAL_CFG_CACHE", None)
    monkeypatch.setattr(su, "_read_encrypted_upload_config", lambda repo: (None, ""))
    monkeypatch.setattr(su, "_read_plaintext_upload_config", lambda repo: ("http://plain/ingest", "pk"))
    monkeypatch.delenv(su._DISABLE_ENC_CFG, raising=False)
    assert su._read_local_upload_config() == ("http://plain/ingest", "pk")


def test_disable_flag_skips_encrypted_path(monkeypatch) -> None:
    monkeypatch.setattr(su, "_LOCAL_CFG_CACHE", None)
    called = {"enc": False}

    def _enc(repo):
        called["enc"] = True
        return ("http://enc/ingest", "ek")

    monkeypatch.setattr(su, "_read_encrypted_upload_config", _enc)
    monkeypatch.setattr(su, "_read_plaintext_upload_config", lambda repo: ("http://plain/ingest", "pk"))
    monkeypatch.setenv(su._DISABLE_ENC_CFG, "1")
    assert su._read_local_upload_config() == ("http://plain/ingest", "pk")
    assert called["enc"] is False  # 加密路径被开关跳过


def test_cache_only_on_success_allows_retry_after_activation(monkeypatch) -> None:
    state = {"activated": False}

    def _enc(repo):
        return ("http://enc/ingest", "ek") if state["activated"] else (None, "")

    monkeypatch.setattr(su, "_read_encrypted_upload_config", _enc)
    monkeypatch.setattr(su, "_read_plaintext_upload_config", lambda repo: (None, ""))
    monkeypatch.delenv(su._DISABLE_ENC_CFG, raising=False)
    monkeypatch.setattr(su, "_LOCAL_CFG_CACHE", None)
    # 首次: 未激活 -> (None, "") 不缓存
    assert su._read_local_upload_config() == (None, "")
    # 激活发生在此之后; 下次结算重试(None 未被缓存) -> 成功
    state["activated"] = True
    assert su._read_local_upload_config() == ("http://enc/ingest", "ek")


# ---- 隐私回归: 无配置不上传 ----

def test_maybe_upload_no_config_means_no_upload(monkeypatch) -> None:
    monkeypatch.delenv("BIDKING_SAMPLE_UPLOAD_URL", raising=False)
    monkeypatch.delenv("BIDKING_SAMPLE_UPLOAD_KEY", raising=False)
    monkeypatch.setattr(su, "_LOCAL_CFG_CACHE", None)
    monkeypatch.setattr(su, "_read_encrypted_upload_config", lambda repo: (None, ""))
    monkeypatch.setattr(su, "_read_plaintext_upload_config", lambda repo: (None, ""))
    monkeypatch.delenv(su._DISABLE_ENC_CFG, raising=False)
    art = {"phase": "settled", "session_id": "s1", "map_id": 2410, "hero": "ahmed",
           "final_quality_counts": {"q5": 1}, "final_quality_cells": {"q5": 5},
           "final_quality_values": {"q5": 100}, "inventory_count": 1, "inventory_cells": 5,
           "known_value_sum": 100, "settlement_incomplete": False}
    assert su.maybe_upload_settlement(art) is False
