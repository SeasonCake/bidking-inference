import copy
import unittest
from research.native_lab.run import replay, verify_identity


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.value = dict(
            variant=0,
            call_result=5,
            function_va=4096 + 512,
            loaded_base=4096,
            rva=512,
            pointer_size=8,
            record_size=24,
            sequence_offset=8,
            count_offset=16,
        )

    def test_known_good_layout_and_address(self):
        verify_identity(self.value, 2048)

    def test_wrong_build_address_and_layout_rejected_before_call(self):
        for changed in [
            {"variant": 1},
            {"rva": 4096},
            {"function_va": 123},
            {"pointer_size": 4},
            {"count_offset": 12},
        ]:
            value = dict(self.value, **changed)
            with self.assertRaises(ValueError):
                verify_identity(value, 2048)

    def test_hand_specified_event_control_and_mutation(self):
        def event(epoch, sequence, facts):
            return dict(
                schema_version=1,
                producer_id="fixture",
                epoch=epoch,
                sequence=sequence,
                facts=facts,
            )

        rows = [
            event(1, 1, {"count": 0, "cells": 2}),
            event(1, 1, {"count": 0, "cells": 2}),
            event(1, 0, {"count": 9}),
            event(0, 8, {"count": 2}),
            event(1, 2, {"count": None}),
            event(1, 3, {}),
            event(2, 1, {"count": 5}),
            event(1, 3, {"count": 1}),
        ]
        self.assertEqual(replay(rows)["counts"]["accepted"], 3)
        wrong = copy.deepcopy(rows)
        wrong[1]["sequence"] = 2
        with self.assertRaises(ValueError):
            replay(wrong)
        with self.assertRaises(ValueError):
            replay(rows[:-1])
