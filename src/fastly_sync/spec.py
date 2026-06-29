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
DEFAULT_RATELIMIT_KEY = "x-fastly-ratelimit"
DEFAULT_CACHE_KEY = "x-fastly-cache"
_DEFAULT_WINDOW = 60
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


def build_desired_state(
    spec: dict[str, Any],
    *,
    cache_key: str = DEFAULT_CACHE_KEY,
    ratelimit_key: str = DEFAULT_RATELIMIT_KEY,
) -> DesiredState:
    """Derive CDN endpoints and rate limiter rules from an OpenAPI spec.

    ``cache_key`` / ``ratelimit_key`` select the OpenAPI extension keys to read
    (defaults: ``x-fastly-cache`` / ``x-fastly-ratelimit``), so custom keys can
    be used in the document.
    """
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
            endpoints.append(_cache_for(path, item, methods, cache_key))
        rule = _rate_limit_for(path, item, methods, ratelimit_key)
        if rule is not None:
            rate_limiters.append(rule)

    return DesiredState(
        endpoints=tuple(endpoints),
        rate_limiters=tuple(rate_limiters),
    )


def _cache_for(
    path: str, item: dict[str, Any], methods: tuple[str, ...], cache_key: str
) -> CdnEndpoint:
    # Idempotent read-only endpoints are cacheable by default; any mutating
    # method forces a pass. An explicit cache extension overrides.
    cacheable = all(method in _CACHEABLE_METHODS for method in methods)
    default_action = "cache" if cacheable else "pass"
    condition_name = f"cache-{_slug(path)}"
    match_statement = _match_statement(path)

    extension = item.get(cache_key)
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
        raise SpecError(f"invalid {cache_key} for '{path}': expected an object")

    action = str(extension.get("action", default_action))
    if action not in _CACHE_ACTIONS:
        raise SpecError(
            f"invalid {cache_key} action '{action}' for '{path}': "
            f"expected one of {sorted(_CACHE_ACTIONS)}"
        )
    try:
        ttl = int(extension.get("ttl", _DEFAULT_TTL if action == "cache" else 0))
        stale_while_revalidate = int(extension.get("stale_while_revalidate", 0))
        stale_if_error = int(extension.get("stale_if_error", 0))
    except (TypeError, ValueError) as exc:
        raise SpecError(f"invalid {cache_key} for '{path}': {exc}") from exc

    return CdnEndpoint(
        path=path,
        methods=methods,
        action=action,
        ttl=ttl,
        stale_while_revalidate=stale_while_revalidate,
        stale_if_error=stale_if_error,
        condition_name=condition_name,
        match_statement=match_statement,
        description=str(extension.get("description", "")),
    )


def _rate_limit_for(
    path: str, item: dict[str, Any], methods: tuple[str, ...], ratelimit_key: str
) -> RateLimiterRule | None:
    extension = item.get(ratelimit_key)
    if not isinstance(extension, dict):
        return None
    try:
        limit = int(extension["limit"])
        window = int(extension.get("window", _DEFAULT_WINDOW))
        penalty_box_duration = int(extension.get("penalty_box_duration", 1))
        feature_revision = int(extension.get("feature_revision", 1))
    except (KeyError, TypeError, ValueError) as exc:
        raise SpecError(f"invalid {ratelimit_key} for '{path}': {exc}") from exc
    return RateLimiterRule(
        name=str(extension.get("name") or _slug(path)),
        path=path,
        limit=limit,
        window=window,
        http_methods=_http_methods(extension.get("http_methods"), methods),
        action=str(extension.get("action", "response")),
        penalty_box_duration=penalty_box_duration,
        client_key=str(extension.get("client_key", "req.http.Fastly-Client-IP")),
        logger_type=str(extension.get("logger_type", "")),
        response_object_name=str(extension.get("response_object_name", "")),
        uri_dictionary_name=str(extension.get("uri_dictionary_name", "")),
        feature_revision=feature_revision,
    )


def _http_methods(value: object, fallback: tuple[str, ...]) -> tuple[str, ...]:
    # Default to the path's HTTP methods (or GET); accept a list or a
    # comma-separated string in the extension.
    if isinstance(value, str):
        return tuple(part.strip().upper() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(method).upper() for method in value)
    return fallback or ("GET",)


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
