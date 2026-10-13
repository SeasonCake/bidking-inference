# -*- coding: utf-8 -*-
"""Strict HTTPS transport using the CA bundle shipped with the client.

Windows browsers validate through Schannel, while the embedded CPython runtime
uses OpenSSL and can inherit a stale or contaminated default CA path.  Release
packages therefore carry a current Mozilla CA bundle and use it explicitly for
every product HTTPS request.  Verification and hostname checks stay mandatory.
"""
from __future__ import annotations

import ssl
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any


PRODUCT_USER_AGENT = "BidKing-Client/1.0"


def _bundled_ca_file() -> Path:
    try:
        import certifi
    except ImportError as exc:
        raise RuntimeError("trusted CA bundle is unavailable") from exc

    path = Path(certifi.where()).resolve()
    if not path.is_file() or path.stat().st_size < 10_000:
        raise RuntimeError("trusted CA bundle is missing or invalid")
    return path


@lru_cache(maxsize=1)
def verified_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(_bundled_ca_file()))
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("strict TLS verification is not enabled")
    return context


def urlopen_verified(target: Any, *, timeout: float):
    """Open a URL; HTTPS always uses the shipped CA bundle, HTTP stays dev-only."""
    if isinstance(target, urllib.request.Request):
        request = target
        if not any(name.lower() == "user-agent" for name, _ in request.header_items()):
            request.add_unredirected_header("User-Agent", PRODUCT_USER_AGENT)
    else:
        request = urllib.request.Request(
            str(target),
            headers={"User-Agent": PRODUCT_USER_AGENT},
        )

    raw_url = request.full_url
    if urllib.parse.urlsplit(raw_url).scheme.lower() == "https":
        return urllib.request.urlopen(
            request,
            timeout=timeout,
            context=verified_ssl_context(),
        )
    return urllib.request.urlopen(request, timeout=timeout)
