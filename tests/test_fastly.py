# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import httpx
import pytest

from fastly_sync.errors import FastlyAPIError
from fastly_sync.fastly import BASE_URL, FastlyClient
from fastly_sync.models import CdnEndpoint, RateLimiterRule


def make_client(handler):
    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport, base_url=BASE_URL)
    return FastlyClient("token", "svc", client=inner), inner


def test_headers_are_set():
    client, inner = make_client(lambda request: httpx.Response(200, json=[]))
    assert inner.headers["Fastly-Key"] == "token"
    assert inner.headers["Accept"] == "application/json"
    client.close()


def test_get_active_version():
    def handler(request):
        return httpx.Response(
            200, json=[{"number": 1, "active": False}, {"number": 2, "active": True}]
        )

    client, _ = make_client(handler)
    assert client.get_active_version() == 2


def test_get_active_version_without_active():
    client, _ = make_client(lambda request: httpx.Response(200, json=[{"number": 1}]))
    with pytest.raises(FastlyAPIError, match="no active version"):
        client.get_active_version()


def test_clone_version():
    client, _ = make_client(lambda request: httpx.Response(200, json={"number": 7}))
    assert client.clone_version(2) == 7


def test_mutating_calls_issue_requests():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content.decode()))
        return httpx.Response(200, json={})

    client, _ = make_client(handler)
    endpoint = CdnEndpoint(
        path="/w",
        methods=("GET",),
        action="cache",
        ttl=60,
        condition_name="cache-w",
        match_statement='req.url ~ "^/w"',
    )
    client.upsert_condition(7, endpoint)
    client.upsert_cache_setting(7, endpoint)
    client.upsert_rate_limiter(7, RateLimiterRule("w", "/w", 100, 60))
    client.activate_version(7)

    methods_paths = {(method, path) for method, path, _ in seen}
    assert ("PUT", "/service/svc/version/7/condition/cache-w") in methods_paths
    assert ("PUT", "/service/svc/version/7/cache_settings//w") in methods_paths
    assert ("PUT", "/service/svc/version/7/rate-limiters/w") in methods_paths
    assert ("PUT", "/service/svc/version/7/activate") in methods_paths
    cache_body = next(
        body for _, path, body in seen if path.endswith("/cache_settings//w")
    )
    assert "cache_condition=cache-w" in cache_body


def test_request_wraps_http_errors():
    client, _ = make_client(lambda request: httpx.Response(500))
    with pytest.raises(FastlyAPIError, match="failed"):
        client.get_active_version()


def test_owned_client_is_closed_on_close():
    client = FastlyClient("token", "svc")
    assert not client._client.is_closed
    client.close()
    assert client._client.is_closed


def test_injected_client_survives_context_manager():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"number": 3})
    )
    inner = httpx.Client(transport=transport, base_url=BASE_URL)
    with FastlyClient("token", "svc", client=inner) as client:
        assert client.clone_version(1) == 3
    assert not inner.is_closed
    inner.close()
