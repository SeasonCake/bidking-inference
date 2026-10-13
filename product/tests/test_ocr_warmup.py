"""OCR engine background warm-up."""

from __future__ import annotations

import bidking_lab.capture.ocr as ocr_mod


def test_start_warmup_is_idempotent(monkeypatch):
    calls: list[str] = []

    def _fake_warm() -> None:
        calls.append("warm")

    monkeypatch.setattr(ocr_mod, "warm_ocr_engine", _fake_warm)
    with ocr_mod._warm_lock:
        ocr_mod._warm_status = "idle"
        ocr_mod._warm_error = None

    ocr_mod.start_ocr_warmup_background()
    ocr_mod.start_ocr_warmup_background()
    assert len(calls) == 1
