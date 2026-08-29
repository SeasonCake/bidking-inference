"""Evidence coverage checks for v3."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from bidking_lab.inference.v3.events import EvidenceEvent, events_from_fatbeans


@dataclass
class EvidenceCoverageReport:
    files: int = 0
    parsed_files: int = 0
    parse_errors: list[tuple[str, str]] = field(default_factory=list)
    events: int = 0
    by_kind: Counter[str] = field(default_factory=Counter)
    by_strength: Counter[str] = field(default_factory=Counter)
    by_constraint: Counter[str] = field(default_factory=Counter)
    by_source: Counter[str] = field(default_factory=Counter)
    unknown: Counter[str] = field(default_factory=Counter)
    pending: Counter[str] = field(default_factory=Counter)
    examples: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_event(self, event: EvidenceEvent, *, file_name: str) -> None:
        source_key = f"{event.source_kind}:{event.source_id}"
        self.events += 1
        self.by_kind[event.source_kind] += 1
        self.by_strength[event.strength] += 1
        self.by_constraint[event.constraint] += 1
        self.by_source[source_key] += 1
        if event.constraint == "unknown" or event.strength == "unknown":
            self.unknown[source_key] += 1
        if event.constraint == "pending" or event.strength == "pending":
            self.pending[source_key] += 1
        if len(self.examples[source_key]) < 5:
            self.examples[source_key].add(file_name)

    def merge(self, other: "EvidenceCoverageReport") -> None:
        self.files += other.files
        self.parsed_files += other.parsed_files
        self.parse_errors.extend(other.parse_errors)
        self.events += other.events
        self.by_kind.update(other.by_kind)
        self.by_strength.update(other.by_strength)
        self.by_constraint.update(other.by_constraint)
        self.by_source.update(other.by_source)
        self.unknown.update(other.unknown)
        self.pending.update(other.pending)
        for key, values in other.examples.items():
            self.examples[key].update(values)

    @property
    def coverage_ok(self) -> bool:
        return not self.unknown and not self.pending

    @property
    def parse_ok(self) -> bool:
        return not self.parse_errors

    @property
    def ok(self) -> bool:
        return self.coverage_ok and self.parse_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "parsed_files": self.parsed_files,
            "parse_errors": [
                {"file": file, "error": error}
                for file, error in self.parse_errors
            ],
            "events": self.events,
            "by_kind": dict(sorted(self.by_kind.items())),
            "by_strength": dict(sorted(self.by_strength.items())),
            "by_constraint": dict(sorted(self.by_constraint.items())),
            "by_source": dict(sorted(self.by_source.items())),
            "unknown": dict(sorted(self.unknown.items())),
            "pending": dict(sorted(self.pending.items())),
            "examples": {
                key: sorted(values)
                for key, values in sorted(self.examples.items())
                if key in self.unknown or key in self.pending
            },
            "coverage_ok": self.coverage_ok,
            "parse_ok": self.parse_ok,
            "ok": self.ok,
        }


def audit_fatbeans_events(
    events: Any,
    *,
    file_name: str = "<events>",
) -> EvidenceCoverageReport:
    report = EvidenceCoverageReport(files=1, parsed_files=1)
    for event in events_from_fatbeans(events):
        report.add_event(event, file_name=file_name)
    return report


def _iter_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    expanded: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            expanded.extend(path.rglob("*.json"))
        elif path.exists():
            expanded.append(path)
    return tuple(sorted(set(expanded)))


def audit_fatbeans_paths(paths: Iterable[str | Path]) -> EvidenceCoverageReport:
    from bidking_lab.live.fatbeans import parse_fatbeans_capture

    report = EvidenceCoverageReport()
    for path in _iter_paths(paths):
        report.files += 1
        try:
            events = parse_fatbeans_capture(path)
        except Exception as exc:
            report.parse_errors.append((str(path), type(exc).__name__))
            continue
        report.merge(audit_fatbeans_events(events, file_name=path.name))
        report.files -= 1
    return report


__all__ = (
    "EvidenceCoverageReport",
    "audit_fatbeans_events",
    "audit_fatbeans_paths",
)
