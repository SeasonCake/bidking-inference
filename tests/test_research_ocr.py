"""Artificial text examples, including known limits of the historical dictionary."""
import unittest
from research.text import normalize_ocr_text


class OcrTests(unittest.TestCase):
    def test_ordered_substitutions_and_duplicate_label(self):
        self.assertEqual(normalize_ocr_text("伊森空间觉知\r\n优品均格：优品均格：2.5\n自色意品"),
                         "伊森：空间觉知\n优品均格：2.5\n白色藏品")

    def test_scan_and_punctuation(self):
        self.assertEqual(normalize_ocr_text("扫猫（2) 轮属 占位款"), "扫描（2） 轮廓 占位数")

    def test_correct_and_unknown_text_preserved(self):
        for text in ("", "未知词 123.45\n第二行", "扫描（2）"):
            self.assertEqual(normalize_ocr_text(text), text)

    def test_context_free_replacement_is_a_known_false_positive(self):
        self.assertEqual(normalize_ocr_text("这件货色不错"), "这件金色不错")

    def test_lone_carriage_return_is_not_normalized(self):
        self.assertEqual(normalize_ocr_text("甲\r乙"), "甲\r乙")

    def test_no_unsupported_unicode_or_case_normalization(self):
        self.assertEqual(normalize_ocr_text("ＡＢＣ abc　１２３"), "ＡＢＣ abc　１２３")


if __name__ == "__main__":
    unittest.main()
