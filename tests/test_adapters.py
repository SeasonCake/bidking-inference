from __future__ import annotations

import unittest

from auction_inference import Candidate, MappingCandidateAdapter, adapt_records


class AdapterTest(unittest.TestCase):
    def test_mapping_adapter_selects_public_fields(self) -> None:
        adapter = MappingCandidateAdapter("name", "prior", ("count", "value"))
        candidate = adapter({"name": "a", "prior": 0.4, "count": 3, "value": 20, "ignored": 9})
        self.assertEqual(candidate.label, "a")
        self.assertEqual(candidate.attributes, {"count": 3, "value": 20})

    def test_missing_mapping_field_fails(self) -> None:
        adapter = MappingCandidateAdapter("name", "prior", ("count",))
        with self.assertRaises(ValueError):
            adapter({"name": "a", "prior": 1})

    def test_adapter_field_names_must_be_unique(self) -> None:
        with self.assertRaises(ValueError):
            MappingCandidateAdapter("name", "name", ("count",))

    def test_adapt_records_requires_unique_labels(self) -> None:
        adapter = MappingCandidateAdapter("name", "prior", ("count",))
        with self.assertRaises(ValueError):
            adapt_records(
                ({"name": "a", "prior": 1, "count": 1}, {"name": "a", "prior": 1, "count": 2}),
                adapter,
            )

    def test_custom_adapter_must_return_candidate(self) -> None:
        with self.assertRaises(TypeError):
            adapt_records((1,), lambda _: "not-a-candidate")
        self.assertEqual(adapt_records((1,), lambda _: Candidate("a", {"x": 1}, 1))[0].label, "a")


if __name__ == "__main__":
    unittest.main()
