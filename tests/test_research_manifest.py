import unittest
from research.records.manifest import (
    differences,
    fingerprints,
    transform,
    verify_transformed,
)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.old = {"a": b"alpha", "b": b"beta", "c": b"gamma"}
        self.new = dict(self.old, d=b"delta")

    def test_new_member_flows_through_transform_and_readback(self):
        manifest = fingerprints(self.new)
        self.assertFalse(
            any(verify_transformed(manifest, transform(manifest, self.new)).values())
        )

    def test_old_pin_and_missing_member_are_rejected_before_transform(self):
        with self.assertRaises(ValueError):
            transform(fingerprints(self.old), self.new)
        with self.assertRaises(ValueError):
            transform(fingerprints(self.new), self.old)

    def test_same_count_wrong_member_is_not_equivalent(self):
        wrong = {"a": b"alpha", "b": b"beta", "c": b"gamma", "e": b"delta"}
        changes = differences(fingerprints(self.new), fingerprints(wrong))
        self.assertEqual(changes["missing"], ["d"])
        self.assertEqual(changes["unexpected"], ["e"])

    def test_transformed_consumer_detects_changed_bytes(self):
        manifest = fingerprints(self.new)
        changed = dict(self.new, a=b"wrong")
        result = verify_transformed(manifest, transform(fingerprints(changed), changed))
        self.assertEqual(result["changed"], ["a"])
