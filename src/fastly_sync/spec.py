# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Load an OpenAPI document (local or remote) and derive the desired state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .errors import SpecError
from .models import CdnEndpoint, DesiredState, RateLimiterRule

_HTTP_SCHEMES = {"http", "https"}
_HTTP_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
}
_RATELIMIT_KEY = "x-fastly-ratelimit"
_DEFAULT_WINDOW = 60


def load_spec(source: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Load and parse an OpenAPI JSON document.

    Args:
        source: a filesystem path or an ``http(s)`` URL.
        client: optional pre-configured client used for remote fetches.

    Raises:
        SpecError: if the document cannot be read, fetched, or parsed.
    """
    parsed = urlparse(source)
    if parsed.scheme in _HTTP_SCHEMES:
        text = _fetch_remote(source, client)
    else:
        text = _read_local(source)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"invalid JSON in OpenAPI spec '{source}': {exc}") from exc

    if not isinstance(data, dict):
        raise SpecError(f"OpenAPI spec '{source}' must be a JSON object")
    return data


def _read_local(source: str) -> str:
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"cannot read OpenAPI spec '{source}': {exc}") from exc


def _fetch_remote(source: str, client: httpx.Client | None) -> str:
    owns_client = client is None
    active = client if client is not None else httpx.Client(timeout=30.0)
    try:
        response = active.get(source)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError as exc:
        raise SpecError(f"cannot fetch OpenAPI spec '{source}': {exc}") from exc
    finally:
        if owns_client:
            active.close()


def build_desired_state(spec: dict[str, Any]) -> DesiredState:
    """Derive CDN endpoints and rate limiter rules from an OpenAPI spec."""
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise SpecError("OpenAPI spec has no 'paths' object")

    endpoints: list[CdnEndpoint] = []
    rate_limiters: list[RateLimiterRule] = []
    for path, item in sorted(paths.items()):
        if not isinstance(item, dict):
            continue
        methods = tuple(
            method.upper() for method in item if method.lower() in _HTTP_METHODS
        )
        if methods:
            endpoints.append(CdnEndpoint(path=path, methods=methods))
        rule = _rate_limit_for(path, item)
        if rule is not None:
            rate_limiters.append(rule)

    return DesiredState(
        endpoints=tuple(endpoints),
        rate_limiters=tuple(rate_limiters),
    )


def _rate_limit_for(path: str, item: dict[str, Any]) -> RateLimiterRule | None:
    extension = item.get(_RATELIMIT_KEY)
    if not isinstance(extension, dict):
        return None
    try:
        limit = int(extension["limit"])
        window = int(extension.get("window", _DEFAULT_WINDOW))
    except (KeyError, TypeError, ValueError) as exc:
        raise SpecError(f"invalid {_RATELIMIT_KEY} for '{path}': {exc}") from exc
    name = str(extension.get("name") or _slug(path))
    return RateLimiterRule(name=name, path=path, limit=limit, window=window)


def _slug(path: str) -> str:
    cleaned = "".join(char if char.isalnum() else "-" for char in path).strip("-")
    return cleaned or "root"
