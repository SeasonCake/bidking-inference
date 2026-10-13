"""Filter Aisha fatbeans samples suitable for grid feel-check (no early total_cells lock)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (SCRIPTS, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from audit_aisha_gap import (  # noqa: E402
    SAMPLE_ROOTS,
    _build_snapshot,
    _events_through_sort,
    _map_id_from_events,
    _prefix_sort_id,
    _settlement_breakdown,
)
from bidking_lab.live.fatbeans import live_batches_from_fatbeans_events, parse_fatbeans_capture  # noqa: E402
from organize_fatbeans_real_samples import sample_meta  # noqa: E402

REPORT_JSON = ROOT / "data/reports/aisha_feel_sample_filter.json"
WHITE_SKILL = 1001034

PUBLIC_INFO_LABELS = {
    200009: "公开·总格",
    200017: "公开·总件",
}


@dataclass
class FeelSampleRow:
    path: str
    map_id: int | None
    map_family: str
    settlement_cells: int
    bid_rounds: int
    eligible: bool
    reject_reasons: list[str] = field(default_factory=list)
    white_public_info: list[str] = field(default_factory=list)


def _map_family(map_id: int | None) -> str:
    if map_id is None:
        return "unknown"
    mid = int(map_id)
    if 2400 <= mid < 2500:
        return "villa"
    if 2500 <= mid < 2600:
        return "shipwreck"
    if 2600 <= mid < 2700:
        return "hidden"
    return "other"


def _skill_ids(snapshot: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    for key in ("skill_reveals", "skill_reveal_rows"):
        rows = snapshot.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("skill_id") is not None:
                out.add(int(row["skill_id"]))
    return out


def _white_skill_snapshot(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int | None]:
    events = parse_fatbeans_capture(path)
    pre_batches = [b for b in live_batches_from_fatbeans_events(events) if b.phase != "settled"]
    map_id = _map_id_from_events(events)
    settlement = _settlement_breakdown(events)
    if not settlement:
        return None, None, map_id
    for index in range(1, len(pre_batches) + 1):
        prefix = pre_batches[:index]
        sort_id = _prefix_sort_id(prefix)
        prefix_events = _events_through_sort(events, sort_id) if sort_id else events
        snap = _build_snapshot(
            hero="aisha",
            events=prefix_events,
            prefix_batches=prefix,
            map_id=map_id,
        )
        if WHITE_SKILL in _skill_ids(snap):
            return snap, settlement, map_id
    return None, settlement, map_id


def _public_info_labels(snapshot: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in snapshot.get("public_info_rows") or ():
        if not isinstance(row, dict):
            continue
        info_id = row.get("info_id")
        value = row.get("value")
        label = PUBLIC_INFO_LABELS.get(int(info_id) if info_id is not None else 0, f"info_{info_id}")
        out.append(f"{label}={value}")
    return out


def _reject_reasons(snapshot: dict[str, Any]) -> list[str]:
    bridge = snapshot.get("structured_ref_inputs") or {}
    reasons: list[str] = []
    if bridge.get("total_cells") is not None:
        reasons.append(f"bridge.total_cells={bridge.get('total_cells')}")
    for fu in bridge.get("field_updates") or ():
        if not isinstance(fu, dict):
            continue
        path = fu.get("path") or []
        path_text = ".".join(str(part) for part in path)
        if "warehouse_total_cells" in path_text:
            reasons.append(f"field:{path_text}={fu.get('value')}")
        elif len(path) >= 2 and path[0] == "session" and path[-1] == "total_cells":
            reasons.append(f"field:{path_text}={fu.get('value')}")
    for row in snapshot.get("public_info_rows") or ():
        if not isinstance(row, dict):
            continue
        if row.get("info_id") == 200009:
            reasons.append(f"public:200009总格={row.get('value')}")
    return reasons


def audit_sample(path: Path) -> FeelSampleRow | None:
    meta = sample_meta(path)
    if meta.hero != "aisha" or meta.sample_class != "valid":
        return None
    try:
        snap, settlement, map_id = _white_skill_snapshot(path)
    except OSError:
        return None
    if snap is None or not settlement:
        return None
    reject = _reject_reasons(snap)
    return FeelSampleRow(
        path=path.name,
        map_id=map_id,
        map_family=_map_family(map_id),
        settlement_cells=int(settlement["total_cells"]),
        bid_rounds=int(meta.rounds),
        eligible=not reject,
        reject_reasons=reject,
        white_public_info=_public_info_labels(snap),
    )


def iter_sample_paths() -> list[Path]:
    out: dict[str, Path] = {}
    for root in SAMPLE_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("fatbeans*aisha*.json")):
            out[str(path.resolve())] = path
    return list(out.values())


def build_filter_report() -> dict[str, Any]:
    rows = [audit_sample(path) for path in iter_sample_paths()]
    rows = [row for row in rows if row is not None]
    eligible = [row for row in rows if row.eligible]
    rejected = [row for row in rows if not row.eligible]
    return {
        "schema_version": 1,
        "eligible_count": len(eligible),
        "rejected_count": len(rejected),
        "reject_criteria": [
            "bridge.total_cells set at 艾莎1白",
            "session.warehouse_total_cells in field_updates",
            "public_info 200009 (总格) at 艾莎1白",
        ],
        "note": "公开·总件(200017) 不过滤，仅在矩阵中标注",
        "eligible": [asdict(row) for row in sorted(eligible, key=lambda item: (item.map_family, item.settlement_cells))],
        "rejected": [asdict(row) for row in sorted(rejected, key=lambda item: item.path)],
    }


def main() -> int:
    report = build_filter_report()
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"eligible={report['eligible_count']} rejected={report['rejected_count']} -> {REPORT_JSON}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
