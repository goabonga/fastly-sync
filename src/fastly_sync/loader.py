# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Read a source document from a local path or an ``http(s)`` URL."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from .errors import SourceError

_HTTP_SCHEMES = {"http", "https"}


def is_remote(source: str) -> bool:
    """Return ``True`` when ``source`` is an ``http(s)`` URL."""
    return urlparse(source).scheme in _HTTP_SCHEMES


def read_source(source: str, *, client: httpx.Client | None = None) -> str:
    """Return the text content of ``source`` (local file or remote URL).

    Args:
        source: a filesystem path or an ``http(s)`` URL.
        client: optional pre-configured client used for remote fetches; when
            omitted, a short-lived client is created and closed.

    Raises:
        SourceError: if the source cannot be read or fetched.
    """
    if is_remote(source):
        return _fetch_remote(source, client)
    return _read_local(source)


def _read_local(source: str) -> str:
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise SourceError(f"cannot read source '{source}': {exc}") from exc


def _fetch_remote(source: str, client: httpx.Client | None) -> str:
    owns_client = client is None
    active = client if client is not None else httpx.Client(timeout=30.0)
    try:
        response = active.get(source)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        raise SourceError(f"cannot fetch source '{source}': {exc}") from exc
    finally:
        if owns_client:
            active.close()
