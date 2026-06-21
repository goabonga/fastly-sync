# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import json

import httpx
import pytest

from fastly_sync.errors import FastlyAPIError
from fastly_sync.fastly import BASE_URL, FastlyClient
from fastly_sync.models import BlockEntry, CdnEndpoint, RateLimiterRule


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


def test_serve_stale_header_sets_surrogate_control():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={})

    client, _ = make_client(handler)
    endpoint = CdnEndpoint(
        path="/w",
        methods=("GET",),
        action="cache",
        ttl=60,
        stale_while_revalidate=30,
        stale_if_error=90,
        condition_name="cache-w",
    )
    client.upsert_serve_stale_header(7, endpoint)
    assert captured["path"] == "/service/svc/version/7/header/serve-stale-cache-w"
    assert "stale-while-revalidate%3D30" in captured["body"]
    assert "stale-if-error%3D90" in captured["body"]
    assert "cache_condition=cache-w" in captured["body"]


def test_create_acl_returns_id():
    client, _ = make_client(lambda request: httpx.Response(200, json={"id": "ACL9"}))
    assert client.create_acl(3, "waf_blocklist") == "ACL9"


def test_get_acl_id_found_and_missing():
    def handler(request):
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json=[{"number": 4, "active": True}])
        return httpx.Response(200, json=[{"name": "waf_blocklist", "id": "ACL9"}])

    client, _ = make_client(handler)
    assert client.get_acl_id("waf_blocklist") == "ACL9"
    assert client.get_acl_id("absent") is None


def test_upsert_vcl_snippet_posts_recv():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={})

    client, _ = make_client(handler)
    client.upsert_vcl_snippet(3, "waf-block", "if (client.ip ~ acl) { error 403; }")
    assert captured["path"] == "/service/svc/version/3/snippet"
    assert "type=recv" in captured["body"]


def test_list_acl_entries_paginates():
    page_one = [{"id": str(i), "ip": f"10.0.0.{i}", "subnet": None} for i in range(100)]

    def handler(request):
        page = request.url.params.get("page")
        return httpx.Response(200, json=page_one if page == "1" else [{"id": "x"}])

    client, _ = make_client(handler)
    entries = client.list_acl_entries("ACL9")
    assert len(entries) == 101


def test_list_acl_entries_empty():
    client, _ = make_client(lambda request: httpx.Response(200, json=[]))
    assert client.list_acl_entries("ACL9") == []


def test_update_acl_entries_batches_ops():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={})

    client, _ = make_client(handler)
    client.update_acl_entries(
        "ACL9",
        [BlockEntry("203.0.113.0", 24, "botnet"), BlockEntry("198.51.100.7")],
        ["e1"],
    )
    assert captured["path"] == "/service/svc/acl/ACL9/entries"
    ops = json.loads(captured["body"])["entries"]
    assert {
        "op": "create",
        "ip": "203.0.113.0",
        "comment": "botnet",
        "subnet": 24,
    } in ops
    assert {"op": "create", "ip": "198.51.100.7", "comment": ""} in ops
    assert {"op": "delete", "id": "e1"} in ops


def test_update_acl_entries_noop_when_empty():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    client, _ = make_client(handler)
    client.update_acl_entries("ACL9", [], [])
    assert calls == []


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
