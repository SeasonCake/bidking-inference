"""Shared redaction helpers for user-exported support diagnostics."""

from __future__ import annotations

import json
import re
from typing import Any, MutableMapping


REDACTED = "<redacted>"
_SENSITIVE_KEYS = frozenset(
    {
        "account",
        "activation_token",
        "api_key",
        "authorization",
        "bidder",
        "cookie",
        "data",
        "device",
        "device_id",
        "email",
        "hostname",
        "ingest_key",
        "machine_guid",
        "master_key",
        "local_player_id",
        "password",
        "payload",
        "payload_b64",
        "phone",
        "player",
        "player_id",
        "player_name",
        "qq",
        "secret",
        "self_name",
        "set_cookie",
        "token",
        "uid",
        "uid_hash",
        "upload_key",
        "user_id",
        "username",
        "winner",
    }
)
_PLAYER_IDENTITY_KEYS = frozenset(
    {
        "bidder",
        "local_player_id",
        "player",
        "player_id",
        "player_name",
        "self_name",
        "winner",
    }
)
_PLAYER_KEYED_MAPS = frozenset({"latest_bids", "player_ranks"})
_PATH_KEYS = frozenset(
    {
        "file",
        "log_dir",
        "package_root",
        "path",
        "raw_capture",
        "raw_capture_jsonl",
        "raw_tables_dir",
        "raw_tables_root",
        "snapshot_path",
        "source_file",
    }
)
_KEY_ASSIGNMENT = re.compile(
    r"(?i)\b(token|authorization|cookie|password|passwd|secret|api[_-]?key|"
    r"master[_-]?key|upload[_-]?key|ingest[_-]?key|data|payload(?:_b64)?|"
    r"device[_-]?id|machine[_-]?guid|"
    r"uid(?:_hash)?|user(?:name|_id)?|player(?:_name)?|winner|bidder|email|phone|qq)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


def _normalized_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _add_player_alias(aliases: MutableMapping[str, str], value: Any) -> None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return
    identity = str(value).strip()
    if not identity or identity in aliases:
        return
    aliases[identity] = f"<player-{len(aliases) + 1}>"


def collect_player_aliases(
    *values: Any,
    aliases: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Collect player identities before redaction so names embedded in text can be removed.

    ``player_ranks`` and ``latest_bids`` use player names as dynamic mapping keys, so a
    conventional sensitive-value redactor cannot see them.  The returned aliases are stable
    within one diagnostic package and deliberately contain no original identifier.
    """
    result: MutableMapping[str, str] = aliases if aliases is not None else {}

    def visit(value: Any, *, parent_key: str = "") -> None:
        if isinstance(value, dict):
            if parent_key in _PLAYER_KEYED_MAPS:
                for item_key, player_record in value.items():
                    _add_player_alias(result, item_key)
                    if parent_key == "player_ranks" and isinstance(player_record, dict):
                        _add_player_alias(result, player_record.get("id"))
            for item_key, item in value.items():
                normalized = _normalized_key(item_key)
                if normalized in _PLAYER_IDENTITY_KEYS:
                    _add_player_alias(result, item)
                visit(item, parent_key=normalized)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item, parent_key=parent_key)

    for value in values:
        visit(value)
    return dict(result)


def collect_player_aliases_from_document(
    text: str,
    *,
    suffix: str = "",
    aliases: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Extend a package-wide alias table from structured JSON/JSONL content."""
    result: MutableMapping[str, str] = aliases if aliases is not None else {}
    suffix = suffix.lower()
    if suffix == ".json":
        try:
            collect_player_aliases(json.loads(text), aliases=result)
        except (json.JSONDecodeError, TypeError):
            pass
    elif suffix == ".jsonl":
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            collect_player_aliases(payload, aliases=result)
    return dict(result)


def redact_text(
    value: Any,
    *,
    player_aliases: MutableMapping[str, str] | None = None,
) -> str:
    """Remove common credentials and local identifiers from arbitrary text."""
    text = str(value or "")
    text = re.sub(
        r"(?i)\bv\d+\|[^\r\n\s]*\.[0-9a-f]{16,}\b",
        "<activation-token>",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer <redacted>", text)
    text = _KEY_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    text = re.sub(
        r"(?i)([?&](?:token|key|secret|password|auth|authorization)=)[^&#\s]+",
        rf"\1{REDACTED}",
        text,
    )
    text = re.sub(
        r"(?i)\b[A-Z]:\\Users\\[^\\/\s]+",
        lambda _match: r"C:\Users\<user>",
        text,
    )
    text = re.sub(r"(?i)(?:/home|/Users)/[^/\s]+", r"/home/<user>", text)
    text = re.sub(r"(?i)\\Device\\NPF_\{[0-9a-f-]{36}\}", "<npcap-device>", text)
    text = re.sub(
        r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "<guid>",
        text,
    )
    text = re.sub(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", "<mac>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", text)
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "<email>", text)
    # Player names can be one or two CJK characters.  In a user-exported support package,
    # privacy takes priority over retaining every unrelated occurrence of such a short
    # string.  Use one longest-first substitution pass so a short identity cannot consume
    # part of a longer one or mutate an alias inserted earlier in this same pass.
    identities = sorted(
        (identity for identity in (player_aliases or {}) if identity),
        key=len,
        reverse=True,
    )
    if identities:
        identity_pattern = re.compile("|".join(re.escape(identity) for identity in identities))
        text = identity_pattern.sub(
            lambda match: (player_aliases or {})[match.group(0)],
            text,
        )
    return text


def _safe_local_path(
    value: Any,
    *,
    player_aliases: MutableMapping[str, str] | None = None,
) -> str:
    text = str(value or "").replace("\\", "/").rstrip("/")
    name = text.rsplit("/", 1)[-1] if text else ""
    return (
        f"<local-path>/{redact_text(name, player_aliases=player_aliases)}"
        if name
        else "<local-path>"
    )


_MONITOR_BRIDGE_OWNERS = frozenset({"structured_ref_inputs", "ahmad_ref_inputs"})
_MONITOR_BRIDGE_SOURCE = "monitor_pre_settlement_field_updates"


def _monitor_logical_path(value: Any) -> list[str] | None:
    """Recognize only the monitor's fixed field-update vocabulary, not local paths."""
    if (
        type(value) not in {list, tuple}
        or len(value) not in {2, 3, 4}
        or not all(type(part) is str for part in value)
    ):
        return None
    path = tuple(value)
    if len(path) == 2 and path[0] == "session":
        valid = path[1] in {
            "total_item_count", "total_count", "total_cells",
            "warehouse_total_cells", "total_avg_cells",
        }
    elif len(path) == 3 and path[0] == "bucket":
        valid = path[1] in {"1", "2", "3", "4", "5", "6"} and path[2] in {
            "avg_cells", "count", "cells", "total_cells", "avg_value",
            "average_value", "value", "value_sum", "total_value",
        }
    elif len(path) == 3 and path[0] == "bucket_split":
        valid = path[1] in {"white", "green"} and path[2] in {
            "avg_cells", "count", "cells", "total_cells", "value", "value_sum", "total_value",
        }
    else:
        # Fixed creators: Victor count sum and Naomi category-scoped count sum.
        valid = path in {
            ("bucket_group", "q4q5q6", "count"),
            ("category_quality_group", "103+107", "q5q6", "count"),
        }
    return list(path) if valid else None


def _redact_monitor_bridge(
    value: dict[str, Any], *, player_aliases: MutableMapping[str, str]
) -> dict[str, Any]:
    # First redact all values normally. Only the verified structural tokens below
    # are preserved, including when a player happens to use the same nickname.
    redacted = redact_payload(value, player_aliases=player_aliases)
    redacted["source"] = _MONITOR_BRIDGE_SOURCE
    for original, projected in zip(value["field_updates"], redacted["field_updates"]):
        if type(original) is dict and type(projected) is dict:
            path = _monitor_logical_path(original.get("path"))
            if path is not None:
                projected["path"] = path
    return redacted


def redact_payload(
    value: Any,
    *,
    key: str = "",
    player_aliases: MutableMapping[str, str] | None = None,
    _player_rank_record: bool = False,
    _allow_monitor_bridge: bool = True,
) -> Any:
    """Recursively redact structured diagnostic payloads while preserving shape."""
    normalized_key = _normalized_key(key)
    if player_aliases is None:
        player_aliases = collect_player_aliases(value)
    if normalized_key in _SENSITIVE_KEYS:
        return REDACTED
    if (
        normalized_key in _PATH_KEYS
        or normalized_key.endswith("_path")
        or normalized_key.endswith("_dir")
        or normalized_key.endswith("_root")
    ):
        if value is None:
            return None
        return _safe_local_path(value, player_aliases=player_aliases)
    if (
        _allow_monitor_bridge
        and type(key) is str
        and key in _MONITOR_BRIDGE_OWNERS
        and type(value) is dict
        and type(value.get("source")) is str
        and value["source"] == _MONITOR_BRIDGE_SOURCE
        and type(value.get("field_updates")) in {list, tuple}
    ):
        return _redact_monitor_bridge(value, player_aliases=player_aliases)
    if isinstance(value, dict):
        keyed_by_player = normalized_key in _PLAYER_KEYED_MAPS
        player_rank_map = normalized_key == "player_ranks"
        redacted: dict[str, Any] = {}
        for item_key, item in value.items():
            output_key = (
                player_aliases.get(str(item_key), "<player>")
                if keyed_by_player
                # Player aliases are only legitimate dynamic keys in the two maps above.
                # Applying one-character aliases to schema keys could corrupt unrelated
                # keys such as ``status`` when a player uses a short ASCII nickname.
                else redact_text(item_key)
            )
            # Production snapshots use player_ranks[name] = {p, t, id}.  ``id`` is
            # sensitive only in that record context; globally redacting every ``id``
            # would erase unrelated map/session/status identifiers from diagnostics.
            if _player_rank_record and _normalized_key(item_key) == "id":
                redacted[output_key] = REDACTED
            else:
                redacted[output_key] = redact_payload(
                    item,
                    key=str(item_key),
                    player_aliases=player_aliases,
                    _player_rank_record=player_rank_map,
                    _allow_monitor_bridge=_allow_monitor_bridge and not keyed_by_player,
                )
        return redacted
    if isinstance(value, list):
        return [
            redact_payload(item, player_aliases=player_aliases, _allow_monitor_bridge=_allow_monitor_bridge)
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            redact_payload(item, player_aliases=player_aliases, _allow_monitor_bridge=_allow_monitor_bridge)
            for item in value
        ]
    if isinstance(value, str):
        return redact_text(value, player_aliases=player_aliases)
    return value


def redact_document(
    text: str,
    *,
    suffix: str = "",
    player_aliases: MutableMapping[str, str] | None = None,
) -> str:
    """Redact JSON/JSONL structurally and other text conservatively."""
    suffix = suffix.lower()
    aliases: MutableMapping[str, str] = player_aliases if player_aliases is not None else {}
    collect_player_aliases_from_document(text, suffix=suffix, aliases=aliases)
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return redact_text(text, player_aliases=aliases)
        return json.dumps(
            redact_payload(payload, player_aliases=aliases), ensure_ascii=False, indent=2
        ) + "\n"
    if suffix == ".jsonl":
        rows: list[str] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                rows.append(redact_text(line, player_aliases=aliases))
            else:
                rows.append(
                    json.dumps(redact_payload(payload, player_aliases=aliases), ensure_ascii=False)
                )
        return ("\n".join(rows) + "\n") if rows else ""
    return redact_text(text, player_aliases=aliases)
