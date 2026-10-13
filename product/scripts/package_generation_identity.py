"""Derive the package generation bound to the monitor process.

The digest is deliberately path-bound to a small fixed runtime set.  It is an
identity value, not a secret: callers may persist the final digest, but never
the absolute paths or file contents used to derive it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


_HEX40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_VERSION_RE = re.compile(
    r"^v?[0-9]+(?:\.[0-9]+){1,2}(?:-(?:hotfix|r)[0-9]+)?$",
    re.ASCII,
)
_SOURCE_VERSION_RE = re.compile(
    rb'^HERO_REF_VERSION = "(?P<version>[0-9A-Za-z.-]+)"\s*$',
    re.MULTILINE,
)
_MAX_MANIFEST_BYTES = 1_048_576
_MISSING_SHA = "missing"
_MANIFEST_IDENTITY_FIELDS = frozenset(
    {
        "PackageVersion",
        "SourceCommit",
        "EncryptedUploadConfig",
        "ProductProfile",
        "Encrypted",
        "NativeBinaryDistribution",
    }
)
_PORTABLE_MEMBER_PATHS = (
    "BidKingLive.exe",
    "runtime/python/pythonw.exe",
    "scripts/run_wss_live_monitor.py",
    "scripts/package_generation_identity.py",
    "scripts/start_live_npcap_overlay.ps1",
    "scripts/mitm_bidking_wss_addon.py",
    "scripts/_local_compat_core.py",
)


@dataclass(frozen=True)
class PackageGenerationIdentity:
    digest: str
    package_version: str
    source_commit: str
    build_manifest_sha256: str
    runtime_fingerprint_sha256: str
    upload_config_sha256: str


def is_generation_digest(value: object) -> bool:
    return type(value) is str and _HEX64_RE.fullmatch(value) is not None  # noqa: E721


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(
    path: Path,
    *,
    label: str,
    require_single_link: bool = False,
) -> Path:
    try:
        metadata = os.lstat(path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError(f"{label} is not a regular file")
        if require_single_link and metadata.st_nlink != 1:
            raise ValueError(f"{label} link topology is invalid")
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    return path.resolve(strict=True)


def _inside_root(
    path: Path,
    root: Path,
    *,
    label: str,
    require_single_link: bool = False,
) -> tuple[Path, str]:
    resolved = _regular_file(
        path,
        label=label,
        require_single_link=require_single_link,
    )
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} is outside the package root") from exc
    return resolved, relative.as_posix()


def _manifest_fields(raw: bytes) -> dict[str, str]:
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise ValueError("build manifest size is invalid")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("build manifest encoding is invalid") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        if key in _MANIFEST_IDENTITY_FIELDS:
            if key in values:
                raise ValueError("build manifest identity field is duplicated")
            if not raw_value.startswith(" "):
                raise ValueError("build manifest identity field is malformed")
            value = raw_value[1:]
            if not value or value != value.strip():
                raise ValueError("build manifest identity field is malformed")
            values[key] = value
    if set(values) != _MANIFEST_IDENTITY_FIELDS:
        raise ValueError("build manifest identity field is missing")
    if _VERSION_RE.fullmatch(values["PackageVersion"]) is None:
        raise ValueError("build manifest package version is invalid")
    commit = values["SourceCommit"].lower()
    if _HEX40_RE.fullmatch(commit) is None:
        raise ValueError("build manifest source commit is invalid")
    values["SourceCommit"] = commit
    if values["EncryptedUploadConfig"] not in {"True", "False"}:
        raise ValueError("build manifest upload-config flag is invalid")
    if values["ProductProfile"] not in {"standard", "network-diagnostic"}:
        raise ValueError("build manifest product profile is invalid")
    if values["Encrypted"] not in {"True", "False"}:
        raise ValueError("build manifest encrypted flag is invalid")
    if values["NativeBinaryDistribution"] not in {"unsigned", "NotRequired"}:
        raise ValueError("build manifest native-binary distribution is invalid")
    portable = (
        values["ProductProfile"] == "standard"
        and values["Encrypted"] == "False"
        and values["NativeBinaryDistribution"] == "NotRequired"
        and values["EncryptedUploadConfig"] == "False"
    )
    high_protection = (
        values["Encrypted"] == "True"
        and values["NativeBinaryDistribution"] == "unsigned"
    )
    if not (portable or high_protection):
        raise ValueError("build manifest protection profile is invalid")
    return values


def _source_tree_fields(root: Path) -> tuple[str, str]:
    version_path = _regular_file(
        root / "hero_ref" / "tools" / "hero_ref_version.py",
        label="source version",
    )
    matches = list(_SOURCE_VERSION_RE.finditer(version_path.read_bytes()))
    if len(matches) != 1:
        raise ValueError("source version identity is ambiguous")
    version = matches[0].group("version").decode("ascii")
    if _VERSION_RE.fullmatch(version) is None:
        raise ValueError("source version identity is invalid")
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="ascii",
        errors="strict",
        check=False,
        timeout=10,
    )
    commit = completed.stdout.strip().lower()
    if completed.returncode or _HEX40_RE.fullmatch(commit) is None:
        raise ValueError("source commit identity is unavailable")
    return version, commit


def compute_package_generation_identity(
    *,
    package_root: Path,
    monitor_path: Path,
    runtime_path: Path,
    identity_helper_path: Path | None = None,
) -> PackageGenerationIdentity:
    root = package_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("package root is invalid")

    monitor, monitor_relative = _inside_root(
        monitor_path, root, label="monitor entrypoint"
    )
    helper, helper_relative = _inside_root(
        Path(__file__) if identity_helper_path is None else identity_helper_path,
        root,
        label="package identity helper",
    )
    launcher, launcher_relative = _inside_root(
        root / "scripts" / "start_live_npcap_overlay.ps1",
        root,
        label="public launcher",
    )
    manifest_path = root / "BUILD_MANIFEST.txt"
    packaged_runtime_members: list[tuple[str, str]] = []
    portable_runtime_members = False
    if manifest_path.exists():
        manifest = _regular_file(
            manifest_path,
            label="build manifest",
            require_single_link=True,
        )
        manifest_raw = manifest.read_bytes()
        fields = _manifest_fields(manifest_raw)
        package_version = fields["PackageVersion"]
        source_commit = fields["SourceCommit"]
        build_manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
        scripts_dir = root / "scripts"
        portable = (
            fields["ProductProfile"] == "standard"
            and fields["Encrypted"] == "False"
            and fields["NativeBinaryDistribution"] == "NotRequired"
        )
        if portable:
            if (
                monitor_relative != "scripts/run_wss_live_monitor.py"
                or helper_relative != "scripts/package_generation_identity.py"
                or launcher_relative != "scripts/start_live_npcap_overlay.ps1"
            ):
                raise ValueError("portable package entrypoint is invalid")
            for relative in _PORTABLE_MEMBER_PATHS:
                member, member_relative = _inside_root(
                    root / relative,
                    root,
                    label="portable package member",
                    require_single_link=True,
                )
                if member_relative != relative:
                    raise ValueError("portable package member path is invalid")
                packaged_runtime_members.append((member_relative, _sha256_file(member)))
            portable_runtime_members = True
            runtime = root / "runtime" / "python" / "pythonw.exe"
            runtime, runtime_relative = _inside_root(
                runtime,
                root,
                label="portable windowed Python runtime",
                require_single_link=True,
            )
            passed_runtime, passed_runtime_relative = _inside_root(
                runtime_path,
                root,
                label="portable runtime argument",
                require_single_link=True,
            )
            if (
                passed_runtime != runtime
                or passed_runtime_relative != "runtime/python/pythonw.exe"
            ):
                raise ValueError("portable runtime argument is invalid")
            require_upload_config = False
        else:
            runtime, runtime_relative = _inside_root(
                runtime_path, root, label="packaged Python runtime"
            )
            for pattern, label in (
                ("_client_launcher*.pyd", "compiled public launcher"),
                ("_local_compat_monitor*.pyd", "compiled monitor"),
                (
                    "_local_compat_addon_loader*.pyd",
                    "WSS operational native loader",
                ),
            ):
                matches = list(scripts_dir.glob(pattern))
                if len(matches) != 1:
                    raise ValueError(f"{label} identity is ambiguous")
                native, native_relative = _inside_root(matches[0], root, label=label)
                if (
                    label == "WSS operational native loader"
                    and native.name != "_local_compat_addon_loader.pyd"
                ):
                    raise ValueError("WSS operational native loader identity is invalid")
                packaged_runtime_members.append((native_relative, _sha256_file(native)))
            addon_bootstrap, addon_bootstrap_relative = _inside_root(
                scripts_dir / "local_compat_addon.py",
                root,
                label="WSS operational bootstrap",
            )
            packaged_runtime_members.append(
                (addon_bootstrap_relative, _sha256_file(addon_bootstrap))
            )
            require_upload_config = fields["EncryptedUploadConfig"] == "True"
    else:
        package_version, source_commit = _source_tree_fields(root)
        build_manifest_sha256 = "0" * 64
        runtime = _regular_file(runtime_path, label="development Python runtime")
        runtime_relative = "external-runtime/python.exe"
        discovery, discovery_relative = _inside_root(
            root / "scripts" / "live_process_discovery.ps1",
            root,
            label="monitor process discovery",
        )
        packaged_runtime_members.append(
            (discovery_relative, _sha256_file(discovery))
        )
        require_upload_config = False

    if portable_runtime_members:
        runtime_members = tuple(packaged_runtime_members)
        if len({relative for relative, _digest in runtime_members}) != len(runtime_members):
            raise ValueError("portable package member identity is duplicated")
        runtime_members = tuple(sorted(runtime_members, key=lambda item: item[0]))
    else:
        runtime_members = (
            (runtime_relative, _sha256_file(runtime)),
            (monitor_relative, _sha256_file(monitor)),
            (helper_relative, _sha256_file(helper)),
            (launcher_relative, _sha256_file(launcher)),
            *packaged_runtime_members,
        )
    runtime_fingerprint = hashlib.sha256()
    for relative, digest in runtime_members:
        runtime_fingerprint.update(relative.encode("utf-8"))
        runtime_fingerprint.update(b"\0")
        runtime_fingerprint.update(digest.encode("ascii"))
        runtime_fingerprint.update(b"\0")
    runtime_fingerprint_sha256 = runtime_fingerprint.hexdigest()

    upload_path = root / "data" / "processed" / "upload_config.enc"
    if manifest_path.exists() and fields["Encrypted"] == "False":
        if upload_path.exists():
            raise ValueError("portable upload config is forbidden")
        upload_config_sha256 = _MISSING_SHA
    elif upload_path.exists():
        upload_config = _regular_file(upload_path, label="encrypted upload config")
        upload_config_sha256 = _sha256_file(upload_config)
    elif require_upload_config:
        raise ValueError("encrypted upload config is missing")
    else:
        upload_config_sha256 = _MISSING_SHA

    canonical = {
        "build_manifest_sha256": build_manifest_sha256,
        "package_version": package_version,
        "runtime_fingerprint_sha256": runtime_fingerprint_sha256,
        "schema_version": 1,
        "source_commit": source_commit,
        "upload_config": {
            "path": "data/processed/upload_config.enc",
            "sha256": upload_config_sha256,
        },
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    digest = hashlib.sha256(encoded).hexdigest()
    return PackageGenerationIdentity(
        digest=digest,
        package_version=package_version,
        source_commit=source_commit,
        build_manifest_sha256=build_manifest_sha256,
        runtime_fingerprint_sha256=runtime_fingerprint_sha256,
        upload_config_sha256=upload_config_sha256,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--package-root", required=True)
    parser.add_argument("--monitor", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--identity-helper")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        identity = compute_package_generation_identity(
            package_root=Path(args.package_root),
            monitor_path=Path(args.monitor),
            runtime_path=Path(args.runtime),
            identity_helper_path=(
                Path(args.identity_helper) if args.identity_helper else None
            ),
        )
    except BaseException as exc:
        print(
            "BIDKING_PACKAGE_GENERATION_IDENTITY_ERROR=" + type(exc).__name__,
            file=sys.stderr,
        )
        return 2
    print("BIDKING_PACKAGE_GENERATION_IDENTITY=" + identity.digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
