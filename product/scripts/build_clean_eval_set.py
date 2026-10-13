"""Build the CLEAN engine-eval sample set = the main-measurement window the tuning must use.

WHY (2026-07-03, author-mandated): a naive `glob(fatbeans/*valid*ahmed*)` mixes eras and sample
types and silently corrupts A/B sweeps. Concretely, a gold/purple false-lock sweep on the raw glob
reported 5 "regressions" — 3 of them were OLD-era / no-date / activity-adjacent contamination that
vanished once the set was restricted to the clean window. ALWAYS build the eval set through this
script (or its `clean_eval_files()`), never a bare glob.

Main-measurement rule = the NEW drop-rate era beginning **2026-06-25 12:00** for every
normal map family. Activity generations are identified by their map-number blocks, not by date
alone; there is no upper date bound for normal maps. Excludes:
  * OLD era  (< 2026-06-25 12:00) — old drop rates, wrong priors.
  * activity maps X511-X520 — shipwreck red is boosted (see activity-shipwreck-red-boost) and the
    isolated captures live in fatbeans_activity_20260702_shipwreck/.
  * red-crate activity maps 2541-2550 / 4541-4550 — isolated under
    fatbeans_activity_20260716_red_crate_shipwreck/.
  * no-date — neither the session index nor the capture packets contain a usable timestamp;
    era is unverifiable, so the sample is excluded from the primary measurement.
  * mixed / INVALID — bad settlement truth.

Date is recovered per session-id from data/sample_manifests/session_dates_*.json; legacy sessions
missing from that index fall back to their earliest packet CaptureTime. Usage:
  python scripts/build_sample_truth_registry.py --write  # refresh strict truth verdicts first
  python scripts/build_clean_eval_set.py [hero]   # default hero=ahmed; prints counts, writes manifest
Or import: `from build_clean_eval_set import clean_eval_files; files = clean_eval_files("ahmed")`.

`candidate_eval_files()` performs only the era/map/name prefilter and is NOT safe for tuning.
`clean_eval_files()` additionally requires a content-hashed verdict from sample_pool_registry.json;
missing or stale registry rows fail closed instead of silently admitting unverified captures.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
TRUTH_REGISTRY = ROOT / "data" / "sample_manifests" / "sample_pool_registry.json"
UNIFIED_CATALOG = ROOT / "data" / "sample_manifests" / "unified_sample_catalog_20260715.json"
MAIN_POOL = ROOT / "data" / "samples" / "fatbeans"
ACTIVITY_POOL = ROOT / "data" / "samples" / "fatbeans_activity_20260702_shipwreck"
RED_CRATE_ACTIVITY_POOL = (
    ROOT / "data" / "samples" / "fatbeans_activity_20260716_red_crate_shipwreck"
)
ALL_EVAL_HEROES = ("ahmed", "aisha", "gabriela", "raven", "ethan", "tatiana", "sophie")
TRUTH_GATE_SOURCE = ROOT / "scripts" / "sample_truth_gate.py"
TRUTH_REGISTRY_SCHEMA_VERSION = 5
TRUTH_GATE_CONTEXT_PATHS = (
    ROOT / "scripts" / "sample_truth_gate.py",
    ROOT / "scripts" / "summarize_fatbeans_sample_manifest.py",
    ROOT / "scripts" / "mitm_bidking_wss_addon.py",
    ROOT / "scripts" / "run_fatbeans_webhook_monitor.py",
    ROOT / "scripts" / "run_wss_live_monitor.py",
    ROOT / "hero_ref" / "src" / "ahmad_ref_engine.py",
    ROOT / "hero_ref" / "src" / "layout_depth_policy.py",
    ROOT / "hero_ref" / "tools" / "ahmad_live_panel_server.py",
    ROOT / "hero_ref" / "tools" / "ahmad_tk_overlay.py",
    ROOT / "hero_ref" / "tools" / "detail_composition_view.py",
    ROOT / "src" / "bidking_lab" / "data" / "item_category_authority.py",
    ROOT / "src" / "bidking_lab" / "inference" / "mean_multiset_support.py",
    ROOT / "src" / "bidking_lab" / "inference" / "size_avg_evidence.py",
    ROOT / "src" / "bidking_lab" / "inference" / "v2.py",
    ROOT / "src" / "bidking_lab" / "live" / "fatbeans.py",
    ROOT / "src" / "bidking_lab" / "live" / "live_refresh_signatures.py",
    ROOT / "src" / "bidking_lab" / "live" / "monitor.py",
    ROOT / "src" / "bidking_lab" / "extract" / "item_table.py",
    ROOT / "src" / "bidking_lab" / "runtime" / "snapshot.py",
    ROOT / "data" / "raw" / "tables" / "BidMap.txt",
    ROOT / "data" / "raw" / "tables" / "Drop.txt",
    ROOT / "data" / "raw" / "tables" / "Item.txt",
    ROOT / "data" / "processed" / "drop_weights_all_maps_0627.json",
    ROOT / "data" / "processed" / "item_category_authority_0_3_2.json",
    ROOT / "data" / "processed" / "items_droppable.json",
    ROOT / "data" / "processed" / "items.json",
    ROOT
    / "external_references"
    / "AuctionAnalyzer4.13.3"
    / "_decompiled"
    / "MapBidCalculator"
    / "MapBidCalculator"
    / "Models"
    / "StaticData.cs",
)
TRUTH_GATE_REQUIRED_RAW_SHA256 = {
    ROOT
    / "external_references"
    / "AuctionAnalyzer4.13.3"
    / "_decompiled"
    / "MapBidCalculator"
    / "MapBidCalculator"
    / "Models"
    / "StaticData.cs": "7543b4aa16addc73dfaaaf3c6df9bc0f6662bd83458de110a16d04ff9f2f9676",
}

WINDOW_LOW = (2026, 6, 25, 12, 0, 0)   # NEW drop rates start (06-25 noon)
WINDOW_HIGH = (2026, 7, 2, 12, 0, 0)   # activity-era reference; not a normal-map upper bound

_SID = re.compile(r"_(\d{16})")
_MAP_RE = re.compile(r"_(?:valid|mixed)_[a-z]+_(\d{3,4})_")


class SampleTruthRegistryError(RuntimeError):
    """The clean pool cannot be proven safe with the current truth registry."""


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_normalized_text_file(path: str | Path) -> str:
    """Hash text semantics, treating LF, CRLF and lone-CR newlines as equivalent.

    Registry sample hashes intentionally remain raw-byte hashes via ``_sha256_file``. This helper is
    only for checked-out source/table context, whose Git newline materialization must not invalidate
    the same logical gate across Windows/Linux checkouts.
    """
    raw = Path(path).read_bytes()
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _truth_gate_code_sha256() -> str:
    return _sha256_normalized_text_file(TRUTH_GATE_SOURCE)


def _truth_gate_context_sha256() -> str:
    """Hash every source/table input that can change a registry verdict."""
    digest = hashlib.sha256()
    for path in TRUTH_GATE_CONTEXT_PATHS:
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        required_raw_sha256 = TRUTH_GATE_REQUIRED_RAW_SHA256.get(path)
        if required_raw_sha256 is not None:
            if not path.is_file():
                raise SampleTruthRegistryError(
                    f"required truth gate context is missing: {relative}"
                )
            actual_raw_sha256 = _sha256_file(path)
            if actual_raw_sha256 != required_raw_sha256:
                raise SampleTruthRegistryError(
                    f"required truth gate context identity mismatch: {relative}"
                )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(_sha256_normalized_text_file(path).encode("ascii"))
        else:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _load_truth_registry() -> dict[str, dict]:
    if not TRUTH_REGISTRY.is_file():
        raise SampleTruthRegistryError(
            "sample truth registry is missing; run "
            "`python scripts/build_sample_truth_registry.py --write --full` before evaluation"
        )
    try:
        payload = json.loads(TRUTH_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SampleTruthRegistryError(f"cannot read sample truth registry: {exc}") from exc
    if payload.get("schema_version") != TRUTH_REGISTRY_SCHEMA_VERSION:
        raise SampleTruthRegistryError(
            f"unsupported sample truth registry schema: {payload.get('schema_version')!r}"
        )
    expected_gate = _truth_gate_code_sha256()
    if payload.get("gate_code_sha256") != expected_gate:
        raise SampleTruthRegistryError(
            "sample truth registry was built with a different truth gate; run "
            "`python scripts/build_sample_truth_registry.py --write --full`"
        )
    expected_context = _truth_gate_context_sha256()
    if payload.get("gate_context_sha256") != expected_context:
        raise SampleTruthRegistryError(
            "sample truth registry parser/engine/table context has changed; run "
            "`python scripts/build_sample_truth_registry.py --write --full`"
        )
    rows = payload.get("samples")
    if not isinstance(rows, list):
        raise SampleTruthRegistryError("sample truth registry has no samples list")
    by_name = {
        str(row["file"]): row
        for row in rows
        if isinstance(row, dict) and row.get("file")
    }
    if len(by_name) != len(rows):
        raise SampleTruthRegistryError("sample truth registry contains invalid or duplicate file rows")
    return by_name


def _registered_valid_files(candidates: Iterable[str | Path], *, selector: str) -> list[str]:
    """Validate a local raw selection against the current content-hashed truth registry."""
    registry = _load_truth_registry()
    out: list[str] = []
    unregistered: list[str] = []
    stale: list[str] = []
    inactive: list[str] = []
    for candidate in candidates:
        path = Path(candidate)
        row = registry.get(path.name)
        if row is None:
            unregistered.append(path.name)
            continue
        if row.get("sha256") != _sha256_file(path):
            stale.append(path.name)
            continue
        if row.get("active") is False:
            inactive.append(path.name)
            continue
        if row.get("verdict") == "valid":
            out.append(str(path))
    if unregistered or stale or inactive:
        details: list[str] = []
        if unregistered:
            details.append(f"unregistered={len(unregistered)} examples={unregistered[:3]}")
        if stale:
            details.append(f"content_drift={len(stale)} examples={stale[:3]}")
        if inactive:
            details.append(f"inactive={len(inactive)} examples={inactive[:3]}")
        raise SampleTruthRegistryError(
            f"sample truth registry does not cover selector {selector!r} ("
            + "; ".join(details)
            + "); run `python scripts/build_sample_truth_registry.py --write --full`"
        )
    return sorted(out)


def _map_of(name: str) -> int | None:
    m = _MAP_RE.search(name)
    return int(m.group(1)) if m else None


def _is_activity_ship(mid: int | None) -> bool:
    # Activity-exclusive shipwreck map numbers X511-X520 (2026-07-06 census). Mirror of
    # ahmad_ref_engine._is_activity_shipwreck_map / monitor._is_activity_shipwreck_map.
    if mid is None:
        return False
    if not (2500 <= mid < 2600 or 4500 <= mid < 4600):
        return False
    return 11 <= (mid % 100) <= 20


def _is_red_crate_activity_ship(mid: int | None) -> bool:
    if mid is None:
        return False
    return 2541 <= mid <= 2550 or 4541 <= mid <= 4550


def _is_clean_era_map(
    dt: tuple[int, int, int, int, int, int] | None, mid: int | None
) -> bool:
    """Accept proven new-rate normal maps while excluding both activity generations."""
    return (
        dt is not None
        and dt >= WINDOW_LOW
        and not _is_activity_ship(mid)
        and not _is_red_crate_activity_ship(mid)
    )


def _load_session_dates() -> dict[str, str]:
    for p in sorted(glob.glob(str(ROOT / "data" / "sample_manifests" / "session_dates_*.json")), reverse=True):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
    return {}


def _capture_date_from_path(path: str | Path) -> str | None:
    """Recover the earliest packet CaptureTime directly from one raw capture.

    Legacy files can be absent from the session-date index while still carrying an exact timestamp.
    ``utf-8-sig`` covers the older PowerShell exports that retained a UTF-8 BOM.
    """
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    stamps = [
        str(row["CaptureTime"])
        for row in rows
        if isinstance(row, dict) and row.get("CaptureTime")
    ]
    if not stamps:
        return None
    return min(stamps)[:19].replace("/", "-").replace("T", " ")


def _dt6(s: str | None) -> tuple[int, int, int, int, int, int] | None:
    if not s:
        return None
    try:
        s = str(s)[:19].replace("T", " ")
        d, t = s.split(" ")
        y, mo, dd = (int(x) for x in d.split("-"))
        hh, mi, ss = (int(x) for x in t.split(":"))
        return (y, mo, dd, hh, mi, ss)
    except Exception:
        return None


def _declared_mismatch_names() -> set[str]:
    """Basenames of samples whose skill/state-DECLARED total item count disagrees with the settled
    count (2026-07-04 audit: 82 ahmed samples, declared>settled up to -30% = settlement lost items;
    the per-item observed/mixed screen can't see them because ahmed's declaration is an aggregate).
    Regenerate via scratchpad audit or scripts/audit_declared_vs_settled when new data lands."""
    p = ROOT / "data" / "sample_manifests" / "declared_mismatch_exclude.json"
    try:
        rows = json.load(open(p, encoding="utf-8"))
    except Exception:
        return set()
    return {Path(r["file"]).name for r in rows if isinstance(r, dict) and r.get("file")}


def candidate_eval_files(hero: str = "ahmed") -> list[str]:
    """Era/map/name-prefiltered candidates. Not safe for tuning without the truth registry."""
    sd = _load_session_dates()
    bad = _declared_mismatch_names()
    out: list[str] = []
    for f in glob.glob(str(MAIN_POOL / f"*valid*{hero}*.json")):
        name = Path(f).name
        if "INVALID" in f or "mixed" in name or name in bad:
            continue
        m = _SID.search(name)
        indexed_date = sd.get(m.group(1)) if m else None
        dt = _dt6(indexed_date or _capture_date_from_path(f))
        mid = _map_of(name)
        if _is_clean_era_map(dt, mid):
            out.append(f)
    return sorted(out)


def clean_eval_files(hero: str = "ahmed") -> list[str]:
    """Strict valid `hero` samples in the measurement window.

    Candidate files must be registered under their exact content SHA-256. Registered INVALID rows
    are excluded; unregistered or content-drifted rows abort the call so a sweep cannot silently run
    on unverified truth.
    """
    return _registered_valid_files(
        candidate_eval_files(hero), selector=f"clean_eval:{hero.lower()}"
    )


def clean_eval_files_many(heroes: Iterable[str] = ALL_EVAL_HEROES) -> list[str]:
    """Strict clean-eval files for an explicit hero set; never includes the activity pool."""
    return sorted({path for hero in heroes for path in clean_eval_files(str(hero).lower())})


def activity_eval_files(hero: str | None = None) -> list[str]:
    """Strict current activity A/B files. Activity use must be an explicit caller choice."""
    if not ACTIVITY_POOL.is_dir():
        return []
    candidates = sorted(ACTIVITY_POOL.glob("fatbeans_valid_*.json"))
    if hero:
        token = f"_valid_{hero.lower()}_"
        candidates = [path for path in candidates if token in path.name.lower()]
    return _registered_valid_files(candidates, selector=f"activity_20260702:{hero or 'all'}")


def red_crate_activity_eval_files(hero: str | None = None) -> list[str]:
    """Strict 2026-07-16 red-crate activity files, never mixed with the 07-02 pool."""
    if not RED_CRATE_ACTIVITY_POOL.is_dir():
        return []
    candidates = sorted(RED_CRATE_ACTIVITY_POOL.glob("fatbeans_valid_*.json"))
    if hero:
        token = f"_valid_{hero.lower()}_"
        candidates = [path for path in candidates if token in path.name.lower()]
    return _registered_valid_files(
        candidates, selector=f"activity_20260716_red_crate:{hero or 'all'}"
    )


def truth_prior_records(
    catalog_path: str | Path = UNIFIED_CATALOG, *, hero: str | None = None
) -> list[dict[str, Any]]:
    """Capability entry for settlement truth/prior consumers.

    Cloud rows remain catalog records; this function never turns them into local raw replay files.
    """
    path = Path(catalog_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"unified sample catalog has no records list: {path}")
    hero_key = hero.lower() if hero else None
    return [
        row
        for row in records
        if isinstance(row, dict)
        and row.get("truth_eligible") is True
        and (hero_key is None or str(row.get("hero") or "").lower() == hero_key)
    ]


def _repo_head() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "<unknown>"


def _source_label(path: Path) -> str:
    try:
        path.resolve().relative_to(RED_CRATE_ACTIVITY_POOL.resolve())
        return "local_activity_20260716_red_crate_raw"
    except ValueError:
        pass
    try:
        path.resolve().relative_to(ACTIVITY_POOL.resolve())
        return "local_activity_20260702_raw"
    except ValueError:
        pass
    try:
        path.resolve().relative_to(MAIN_POOL.resolve())
        return "local_main_raw"
    except ValueError:
        return "explicit_external_or_other"


def sample_selection_metadata(
    files: Iterable[str | Path], *, selector: str
) -> dict[str, Any]:
    """Describe the exact population behind one audit/tuning run."""
    paths = sorted({Path(path).resolve() for path in files})
    registry = _load_truth_registry()
    dates: list[str] = []
    unknown_time = 0
    heroes: Counter[str] = Counter()
    registered = 0
    for path in paths:
        row = registry.get(path.name)
        if row is not None:
            registered += 1
            if row.get("hero"):
                heroes[str(row["hero"]).lower()] += 1
            if row.get("capture_date"):
                dates.append(str(row["capture_date"]))
            else:
                unknown_time += 1
        else:
            unknown_time += 1
    source_counts = Counter(_source_label(path) for path in paths)
    activity_count = sum(
        source_counts.get(label, 0)
        for label in (
            "local_activity_20260702_raw",
            "local_activity_20260716_red_crate_raw",
        )
    )
    return {
        "selector": selector,
        "sources": dict(sorted(source_counts.items())),
        "sample_count": len(paths),
        "registered_count": registered,
        "capture_time_min": min(dates) if dates else None,
        "capture_time_max": max(dates) if dates else None,
        "unknown_time_count": unknown_time,
        "commit": _repo_head(),
        "activity": bool(paths) and activity_count == len(paths),
        "activity_count": activity_count,
        "heroes": dict(sorted(heroes.items())),
    }


def selection_row_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """Flat, serialization-friendly run fields for JSONL/CSV result rows."""
    return {
        "sample_selector": metadata["selector"],
        "sample_sources": ";".join(
            f"{key}={value}" for key, value in metadata["sources"].items()
        ),
        "sample_count": metadata["sample_count"],
        "sample_capture_time_min": metadata["capture_time_min"],
        "sample_capture_time_max": metadata["capture_time_max"],
        "sample_unknown_time_count": metadata["unknown_time_count"],
        "sample_commit": metadata["commit"],
        "sample_activity_count": metadata["activity_count"],
    }


def main(argv: list[str]) -> int:
    hero = argv[1] if len(argv) > 1 else "ahmed"
    sd = _load_session_dates()
    bad = _declared_mismatch_names()
    total = old = activity_map = red_crate_activity_map = nodate = declared_mismatch = candidates = 0
    for f in glob.glob(str(MAIN_POOL / f"*valid*{hero}*.json")):
        name = Path(f).name
        if "INVALID" in f or "mixed" in name:
            continue
        total += 1
        if name in bad:
            declared_mismatch += 1
            continue
        m = _SID.search(name)
        indexed_date = sd.get(m.group(1)) if m else None
        dt = _dt6(indexed_date or _capture_date_from_path(f))
        if dt is None:
            nodate += 1
        elif dt < WINDOW_LOW:
            old += 1
        else:
            mid = _map_of(name)
            if _is_activity_ship(mid):
                activity_map += 1
            elif _is_red_crate_activity_ship(mid):
                red_crate_activity_map += 1
            else:
                candidates += 1
    files = clean_eval_files(hero)
    out_path = ROOT / "data" / "sample_manifests" / f"clean_eval_{hero}.json"
    out_path.write_text(json.dumps([str(Path(f).relative_to(ROOT)).replace("\\", "/") for f in files],
                                   ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{hero}: total valid={total}")
    print(f"  measurement candidates={candidates} (all normal maps; no upper date bound)")
    print(
        "  excluded: "
        f"OLD(<06-25 noon)={old}  activity-map(X511-X520)={activity_map}  "
        f"red-crate-activity(2541-2550/4541-4550)={red_crate_activity_map}  "
        f"no-date={nodate}  declared-mismatch={declared_mismatch}"
    )
    print(f"  wrote {len(files)} -> {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
