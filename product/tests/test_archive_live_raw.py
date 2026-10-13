from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(name: str):
    # scripts/ 不是包（无 __init__.py），且 pyproject pythonpath 只含 src/，
    # 所以 `from scripts.x import` 在 pytest 下无法解析。按文件路径加载，与
    # test_fatbeans_webhook_monitor.py 一致；脚本自身的 sys.path 注入仍会执行。
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


archive_live_raw = _load_script_module("archive_live_raw").archive_live_raw


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field_varint(field_no: int, value: int) -> bytes:
    return _varint((field_no << 3) | 0) + _varint(value)


def _field_bytes(field_no: int, value: bytes) -> bytes:
    return _varint((field_no << 3) | 2) + _varint(len(value)) + value


def _send_frame(
    message_id: int,
    *,
    session: str,
    value: int = 123456,
) -> bytes:
    body = _field_varint(1, 1285603971496202)
    body += _field_bytes(2, session.encode("utf-8"))
    body += _field_varint(3, value)
    frame_len = 12 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x5f"
        + message_id.to_bytes(4, "big")
        + body
    )


def _rev_state_frame(
    message_id: int = 0x0025,
    *,
    session: str,
    map_id: int = 2510,
    round_no: int | None = 1,
) -> bytes:
    payload = _field_bytes(1, session.encode("utf-8"))
    payload += _field_varint(2, map_id)
    if round_no is not None:
        payload += _field_varint(3, round_no)
    if message_id in (0x0021, 0x0025):
        body = _field_bytes(1, payload)
    elif message_id == 0x002D:
        body = _field_varint(1, 393075406931726)
        body += _field_bytes(2, payload)
    else:
        raise ValueError(f"unsupported state message id: {message_id}")
    frame_len = 16 + len(body)
    return (
        frame_len.to_bytes(4, "big")
        + b"\x32\xd2\x55\x60"
        + b"\x00\x00\x00\x00"
        + message_id.to_bytes(4, "big")
        + body
    )


def _row(
    direction: str,
    payload: bytes,
    *,
    sort_id: int,
    session: str,
    message_id: int,
) -> dict[str, object]:
    return {
        "SortID": sort_id,
        "Direct": direction,
        "Protocol": "Tcp",
        "SrcIP": "8.133.195.27" if direction == "REV" else "198.18.0.1",
        "SrcPort": 10000 if direction == "REV" else 60213,
        "DstIP": "198.18.0.1" if direction == "REV" else "8.133.195.27",
        "DstPort": 60213 if direction == "REV" else 10000,
        "CaptureTime": f"2026-06-04 00:34:0{sort_id}.000",
        "Data": base64.b64encode(payload).decode("ascii"),
        "DataLength": len(payload),
        "PID": 1234,
        "ProcessName": "BidKing.exe",
        "Source": "WinDivertFrameGate",
        "MessageID": f"0x{message_id:04x}",
        "SessionID": session,
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _write_json_array(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def test_archive_live_raw_classifies_complete_and_dedupes(tmp_path: Path) -> None:
    session = "2510:9000000000000007"
    source = tmp_path / "windivert_live.jsonl"
    rows = [
        _row(
            "SEND",
            _send_frame(0x0022, session=session),
            sort_id=1,
            session=session,
            message_id=0x0022,
        ),
        _row(
            "REV",
            _rev_state_frame(0x0025, session=session),
            sort_id=2,
            session=session,
            message_id=0x0025,
        ),
        _row(
            "REV",
            _rev_state_frame(0x002D, session=session, round_no=2),
            sort_id=3,
            session=session,
            message_id=0x002D,
        ),
    ]
    _write_jsonl(source, rows)

    summary = archive_live_raw(source, archive_dir=tmp_path / "archive")

    assert summary.classification == "complete"
    assert summary.archived is True
    assert summary.duplicate is False
    assert summary.session_id == session
    assert summary.map_id == 2510
    assert "0x002d" in summary.message_ids
    archived = Path(summary.archive_path)
    assert archived.exists()
    assert archived.parent.name == "complete"
    assert len(json.loads(archived.read_text(encoding="utf-8"))) == 3

    duplicate = archive_live_raw(source, archive_dir=tmp_path / "archive")

    assert duplicate.archived is False
    assert duplicate.duplicate is True
    assert duplicate.archive_path == summary.archive_path


def test_archive_live_raw_accepts_json_array_reset_backup(tmp_path: Path) -> None:
    session = "2401:9000000000000009"
    source = tmp_path / "windivert_live_reset.json"
    rows = [
        _row(
            "REV",
            _rev_state_frame(0x0021, session=session, map_id=2401, round_no=None),
            sort_id=1,
            session=session,
            message_id=0x0021,
        ),
        _row(
            "REV",
            _rev_state_frame(0x002D, session=session, map_id=2401, round_no=3),
            sort_id=2,
            session=session,
            message_id=0x002D,
        ),
    ]
    _write_json_array(source, rows)

    summary = archive_live_raw(source, archive_dir=tmp_path / "archive")

    assert summary.classification == "complete"
    assert summary.archived is True
    assert summary.session_id == session
    assert Path(summary.archive_path).parent.name == "complete"


def test_archive_live_raw_classifies_partial_without_settlement(
    tmp_path: Path,
) -> None:
    session = "2401:111"
    source = tmp_path / "windivert_live.jsonl"
    rows = [
        _row(
            "SEND",
            _send_frame(0x0022, session=session),
            sort_id=1,
            session=session,
            message_id=0x0022,
        ),
        _row(
            "REV",
            _rev_state_frame(0x0025, session=session, map_id=2401),
            sort_id=2,
            session=session,
            message_id=0x0025,
        ),
    ]
    _write_jsonl(source, rows)

    summary = archive_live_raw(source, archive_dir=tmp_path / "archive")

    assert summary.classification == "partial"
    assert summary.archived is True
    assert Path(summary.archive_path).parent.name == "partial"
    assert "state frames present" in summary.reason
