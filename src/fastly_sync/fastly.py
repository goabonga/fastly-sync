# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""A thin, typed wrapper over the subset of the Fastly API we drive."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from .errors import FastlyAPIError
from .models import CdnEndpoint, RateLimiterRule

BASE_URL = "https://api.fastly.com"


class FastlyClient:
    """Minimal Fastly API client scoped to a single service.

    The client owns its underlying :class:`httpx.Client` unless one is
    injected (useful for tests), in which case the caller keeps ownership.
    """

    def __init__(
        self,
        token: str,
        service_id: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._service_id = service_id
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.Client(base_url=base_url, timeout=30.0)
        )
        self._client.headers.update({"Fastly-Key": token, "Accept": "application/json"})

    def __enter__(self) -> FastlyClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client if this instance owns it."""
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FastlyAPIError(f"Fastly API {method} {url} failed: {exc}") from exc
        return response

    def get_active_version(self) -> int:
        """Return the number of the currently active service version."""
        response = self._request("GET", f"/service/{self._service_id}/version")
        for version in response.json():
            if version.get("active"):
                return int(version["number"])
        raise FastlyAPIError(f"service '{self._service_id}' has no active version")

    def clone_version(self, version: int) -> int:
        """Clone a version and return the new, editable version number."""
        response = self._request(
            "PUT", f"/service/{self._service_id}/version/{version}/clone"
        )
        return int(response.json()["number"])

    def upsert_condition(self, version: int, endpoint: CdnEndpoint) -> None:
        """Create or update the cache condition scoping an endpoint's path."""
        self._request(
            "PUT",
            f"/service/{self._service_id}/version/{version}/condition/{endpoint.condition_name}",
            data={
                "name": endpoint.condition_name,
                "statement": endpoint.match_statement,
                "type": "CACHE",
                "priority": 10,
            },
        )

    def upsert_cache_setting(self, version: int, endpoint: CdnEndpoint) -> None:
        """Create or update the cache setting backing a CDN endpoint.

        The setting is scoped to the endpoint's path through its
        ``cache_condition``. ``stale_if_error`` maps to Fastly's ``stale_ttl``
        (serve-stale window); ``stale_while_revalidate`` is carried on the model
        for reporting but is not a native ``cache_settings`` field.
        """
        self._request(
            "PUT",
            f"/service/{self._service_id}/version/{version}/cache_settings/{endpoint.path}",
            data={
                "name": endpoint.path,
                "action": endpoint.action,
                "ttl": endpoint.ttl,
                "stale_ttl": endpoint.stale_if_error,
                "cache_condition": endpoint.condition_name,
            },
        )

    def upsert_rate_limiter(self, version: int, rule: RateLimiterRule) -> None:
        """Create or update a rate limiter rule."""
        self._request(
            "PUT",
            f"/service/{self._service_id}/version/{version}/rate-limiters/{rule.name}",
            data={
                "name": rule.name,
                "rps_limit": rule.limit,
                "window_size": rule.window,
            },
        )

    def upsert_serve_stale_header(self, version: int, endpoint: CdnEndpoint) -> None:
        """Set a ``Surrogate-Control`` header carrying the serve-stale windows.

        This is how ``stale_while_revalidate`` is actually applied (it is not a
        native ``cache_settings`` field); ``stale_if_error`` is included too so
        the directive is self-contained. The header is scoped to the endpoint
        through its cache condition.
        """
        directives = []
        if endpoint.stale_while_revalidate:
            directives.append(
                f"stale-while-revalidate={endpoint.stale_while_revalidate}"
            )
        if endpoint.stale_if_error:
            directives.append(f"stale-if-error={endpoint.stale_if_error}")
        name = f"serve-stale-{endpoint.condition_name}"
        self._request(
            "PUT",
            f"/service/{self._service_id}/version/{version}/header/{name}",
            data={
                "name": name,
                "type": "cache",
                "action": "set",
                "dst": "http.Surrogate-Control",
                "src": ", ".join(directives),
                "cache_condition": endpoint.condition_name,
                "priority": 10,
            },
        )

    def activate_version(self, version: int) -> None:
        """Activate a service version, making its configuration live."""
        self._request("PUT", f"/service/{self._service_id}/version/{version}/activate")
