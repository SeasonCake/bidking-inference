"""Small adapter contracts for turning public records into generic candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Protocol, TypeVar

from .scoring import Candidate


RecordT = TypeVar("RecordT")


class CandidateAdapter(Protocol[RecordT]):
    def __call__(self, record: RecordT) -> Candidate: ...


@dataclass(frozen=True)
class MappingCandidateAdapter:
    label_field: str
    prior_field: str
    attribute_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        fields = (self.label_field, self.prior_field, *self.attribute_fields)
        if any(not isinstance(field, str) or not field.strip() for field in fields):
            raise ValueError("adapter field names must be non-empty strings")
        if not self.attribute_fields:
            raise ValueError("at least one attribute field is required")
        if len(set(fields)) != len(fields):
            raise ValueError("adapter field names must be unique")

    def __call__(self, record: Mapping[str, object]) -> Candidate:
        if not isinstance(record, Mapping):
            raise TypeError("record must be a mapping")
        required = {self.label_field, self.prior_field, *self.attribute_fields}
        missing = required.difference(record)
        if missing:
            raise ValueError(f"record is missing fields: {sorted(missing)!r}")
        return Candidate(
            label=record[self.label_field],
            prior=record[self.prior_field],
            attributes={field: record[field] for field in self.attribute_fields},
        )


def adapt_records(
    records: Iterable[RecordT], adapter: CandidateAdapter[RecordT]
) -> tuple[Candidate, ...]:
    candidates: list[Candidate] = []
    for record in records:
        candidate = adapter(record)
        if not isinstance(candidate, Candidate):
            raise TypeError("adapter must return Candidate values")
        candidates.append(candidate)
    if not candidates:
        raise ValueError("at least one record is required")
    labels = tuple(candidate.label for candidate in candidates)
    if len(set(labels)) != len(labels):
        raise ValueError("adapted candidate labels must be unique")
    return tuple(candidates)
