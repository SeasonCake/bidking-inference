"""Optional logging for the capture pipeline (OCR → parse → apply)."""

from __future__ import annotations

import logging
import os

LOG = logging.getLogger("bidking_lab.capture")


def configure_capture_logging(level: int | None = None) -> None:
    """Enable capture logs when ``BIDKING_CAPTURE_LOG=1`` or *level* is set."""
    if level is None:
        level = logging.INFO if os.environ.get("BIDKING_CAPTURE_LOG") else logging.WARNING
    LOG.setLevel(level)
    if level <= logging.DEBUG and not LOG.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"),
        )
        LOG.addHandler(handler)
