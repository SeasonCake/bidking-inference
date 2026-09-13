import copy
import unittest
from research.records.compare import compare, flatten
from research.records.events import BoundedQueue, EpochConsumer


def event(epoch=1, sequence=1, facts=None, **changes):
    value = dict(
        schema_version=1,
        producer_id="toy",
        epoch=epoch,
        sequence=sequence,
        facts={"count": 0} if facts is None else facts,
    )
    return dict(value, **changes)


class RecordTests(unittest.TestCase):
    def test_presence_null_zero_false_are_distinct(self):
        self.assertEqual(compare({"n": None}, {})[0].status, "left_only")
        self.assertEqual(compare({}, {"n": None})[-1].status, "right_only")
        for left, right in [(None, 0), (0, False), (1, 1.0), ("0", 0)]:
            self.assertEqual(compare({"n": left}, {"n": right})[0].status, "different")

    def test_tuple_paths_prevent_dotted_key_collision(self):
        rows = flatten({"a.b": 1, "a": {"b": 2}})
        self.assertEqual(rows, {("a.b",): 1, ("a", "b"): 2})

    def test_empty_objects_arrays_and_order_are_not_erased(self):
        self.assertNotEqual(flatten({"a": {}}), flatten({"a": []}))
        self.assertTrue(compare({"a": [1, 2]}, {"a": [2, 1]}))
        self.assertFalse(compare({"b": 1, "a": None}, {"a": None, "b": 1}))

    def test_budgets_cover_nested_arrays_and_cycles(self):
        with self.assertRaises(ValueError):
            flatten({"a": [[[[1]]]]}, max_depth=3)
        with self.assertRaises(ValueError):
            flatten({"a": list(range(10))}, max_nodes=5)
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaises(ValueError):
            flatten({"a": cyclic})
        with self.assertRaises(ValueError):
            flatten({"a": float("nan")})

    def test_inputs_not_mutated(self):
        a = {"a": [1, {"b": None}]}
        b = copy.deepcopy(a)
        compare(a, b)
        self.assertEqual(a, b)


class EventTests(unittest.TestCase):
    def test_duplicate_conflict_and_out_of_order_do_not_overwrite(self):
        c = EpochConsumer(1)
        self.assertEqual(c.accept(event(sequence=2)), "accepted")
        self.assertEqual(c.accept(event(sequence=2)), "duplicate")
        self.assertEqual(c.accept(event(sequence=2, facts={"count": 8})), "conflict")
        self.assertEqual(
            c.accept(event(sequence=1, facts={"count": 9})), "stale_sequence"
        )
        self.assertEqual(c.facts, {"count": 0})

    def test_epoch_is_owner_controlled_and_old_fields_clear(self):
        c = EpochConsumer(1)
        c.accept(event(facts={"count": 3, "cells": 7}))
        self.assertEqual(c.accept(event(epoch=2)), "future_epoch")
        c.activate(2)
        self.assertEqual(c.facts, {})
        self.assertEqual(c.accept(event(epoch=1, sequence=99)), "stale_epoch")
        self.assertEqual(c.accept(event(epoch=2, facts={})), "accepted")
        self.assertEqual(c.facts, {})
        c.accept(event(epoch=2, sequence=2, facts={"count": None}))
        self.assertEqual(c.facts, {"count": None})

    def test_schema_rejections_and_close(self):
        c = EpochConsumer(1)
        for item in [
            event(schema_version=True),
            event(epoch=True),
            event(sequence=-1),
            event(facts={"count": False}),
            event(facts={"private-field": 1}),
            {},
        ]:
            self.assertEqual(c.accept(item), "schema")
        c.close()
        self.assertEqual(c.accept(event()), "closed")
        with self.assertRaises(ValueError):
            c.activate(2)

    def test_queue_reports_overflow_and_owns_snapshot(self):
        c = EpochConsumer(1)
        q = BoundedQueue(1)
        item = event()
        self.assertTrue(q.put(item))
        item["facts"]["count"] = 99
        self.assertFalse(q.put(event(sequence=2)))
        self.assertEqual(q.rejected, 1)
        self.assertEqual(q.drain(c), ["accepted"])
        self.assertEqual(c.facts, {"count": 0})
        self.assertEqual(q.drain(c), [])
