# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Load an OpenAPI document (local or remote) and derive the desired state."""

from __future__ import annotations

import json
import re
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
_CACHE_KEY = "x-fastly-cache"
_CACHE_ACTIONS = {"cache", "pass"}
_CACHEABLE_METHODS = {"GET", "HEAD"}
_DEFAULT_TTL = 3600


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
            endpoints.append(_cache_for(path, item, methods))
        rule = _rate_limit_for(path, item)
        if rule is not None:
            rate_limiters.append(rule)

    return DesiredState(
        endpoints=tuple(endpoints),
        rate_limiters=tuple(rate_limiters),
    )


def _cache_for(
    path: str, item: dict[str, Any], methods: tuple[str, ...]
) -> CdnEndpoint:
    # Idempotent read-only endpoints are cacheable by default; any mutating
    # method forces a pass. An explicit x-fastly-cache extension overrides.
    cacheable = all(method in _CACHEABLE_METHODS for method in methods)
    default_action = "cache" if cacheable else "pass"
    slug = _slug(path)
    condition_name = f"cache-{slug}"
    match_statement = f'req.url ~ "^{re.escape(_static_prefix(path))}"'

    extension = item.get(_CACHE_KEY)
    if extension is None:
        return CdnEndpoint(
            path=path,
            methods=methods,
            action=default_action,
            ttl=_DEFAULT_TTL if default_action == "cache" else 0,
            condition_name=condition_name,
            match_statement=match_statement,
        )
    if not isinstance(extension, dict):
        raise SpecError(f"invalid {_CACHE_KEY} for '{path}': expected an object")

    action = str(extension.get("action", default_action))
    if action not in _CACHE_ACTIONS:
        raise SpecError(
            f"invalid {_CACHE_KEY} action '{action}' for '{path}': "
            f"expected one of {sorted(_CACHE_ACTIONS)}"
        )
    try:
        ttl = int(extension.get("ttl", _DEFAULT_TTL if action == "cache" else 0))
        stale_while_revalidate = int(extension.get("stale_while_revalidate", 0))
        stale_if_error = int(extension.get("stale_if_error", 0))
    except (TypeError, ValueError) as exc:
        raise SpecError(f"invalid {_CACHE_KEY} for '{path}': {exc}") from exc

    return CdnEndpoint(
        path=path,
        methods=methods,
        action=action,
        ttl=ttl,
        stale_while_revalidate=stale_while_revalidate,
        stale_if_error=stale_if_error,
        condition_name=condition_name,
        match_statement=match_statement,
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
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-")
    return cleaned or "root"


def _static_prefix(path: str) -> str:
    # Match on the literal portion before the first path parameter, so
    # "/widgets/{id}" scopes to "^/widgets/" rather than the literal "{id}".
    prefix = path.split("{", 1)[0]
    return prefix or "/"
