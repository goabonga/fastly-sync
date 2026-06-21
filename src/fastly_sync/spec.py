# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Load an OpenAPI document (local or remote) and derive the desired state."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .errors import SpecError
from .loader import read_source
from .models import CdnEndpoint, DesiredState, RateLimiterRule

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
        SourceError: if the document cannot be read or fetched.
        SpecError: if the document is not valid JSON or not a JSON object.
    """
    text = read_source(source, client=client)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"invalid JSON in OpenAPI spec '{source}': {exc}") from exc

    if not isinstance(data, dict):
        raise SpecError(f"OpenAPI spec '{source}' must be a JSON object")
    return data


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
    condition_name = f"cache-{_slug(path)}"
    match_statement = _match_statement(path)

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


_PARAM_RE = re.compile(r"\{[^/}]+\}")


def _path_regex(path: str) -> str:
    # Build a strict regex: literal segments are escaped, each {param} becomes
    # a single non-slash segment. So "/widgets/{id}" -> "/widgets/[^/]+", which
    # cannot overlap a sibling like "/widget".
    parts: list[str] = []
    last = 0
    for match in _PARAM_RE.finditer(path):
        parts.append(re.escape(path[last : match.start()]))
        parts.append(r"[^/]+")
        last = match.end()
    parts.append(re.escape(path[last:]))
    return "".join(parts)


def _match_statement(path: str) -> str:
    # Anchor both ends: "^<regex>" and "(?:\?|$)" so the match stops at the end
    # of the path or the start of the query string, never on a longer sibling.
    return 'req.url ~ "^' + _path_regex(path) + r'(?:\?|$)"'
