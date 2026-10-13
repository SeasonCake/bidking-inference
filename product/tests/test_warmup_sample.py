"""OCR warm-up sample file on disk."""

from pathlib import Path

from bidking_lab.capture.screen import OCR_WARMUP_SAMPLE


def test_warmup_sample_exists_and_reasonable_size() -> None:
    assert OCR_WARMUP_SAMPLE.is_file(), f"missing {OCR_WARMUP_SAMPLE}"
    size = OCR_WARMUP_SAMPLE.stat().st_size
    assert size < 2_000_000, f"warmup sample too large for git: {size} bytes"
    assert OCR_WARMUP_SAMPLE.suffix.lower() in {".jpg", ".jpeg", ".png"}
