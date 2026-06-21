# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import httpx

from fastly_sync.fastly import BASE_URL, FastlyClient
from fastly_sync.models import BlockEntry
from fastly_sync.show import gather


def _client(handler):
    inner = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    return FastlyClient("token", "svc", client=inner)


def _full_handler(request):
    path = request.url.path
    if path.endswith("/version") and request.method == "GET":
        return httpx.Response(200, json=[{"number": 4, "active": True}])
    if path.endswith("/cache_settings"):
        return httpx.Response(
            200,
            json=[
                {
                    "name": "/widgets",
                    "action": "cache",
                    "ttl": 60,
                    "cache_condition": "cache-widgets",
                },
                {"name": "/manual", "action": "pass", "cache_condition": "other"},
            ],
        )
    if path.endswith("/rate-limiters"):
        return httpx.Response(
            200,
            json=[
                {
                    "id": "r1",
                    "name": "fsync-widgets",
                    "rps_limit": 100,
                    "window_size": 60,
                },
                {"id": "r2", "name": "manual", "rps_limit": 5, "window_size": 1},
            ],
        )
    if path.endswith("/acl"):
        return httpx.Response(200, json=[{"name": "waf_blocklist", "id": "ACL1"}])
    if "/acl/ACL1/entries" in path:
        return httpx.Response(
            200,
            json=[{"id": "e1", "ip": "198.51.100.7", "subnet": None, "comment": "bad"}],
        )
    return httpx.Response(200, json=[])


def test_gather_reports_only_owned_objects():
    config = gather(_client(_full_handler))
    assert config.version == 4
    assert [c["name"] for c in config.cache_settings] == ["/widgets"]
    assert [r["name"] for r in config.rate_limiters] == ["fsync-widgets"]
    assert config.blocklist == (BlockEntry("198.51.100.7", None, "bad"),)


def test_gather_without_acl_has_empty_blocklist():
    def handler(request):
        path = request.url.path
        if path.endswith("/version") and request.method == "GET":
            return httpx.Response(200, json=[{"number": 1, "active": True}])
        if path.endswith("/acl"):
            return httpx.Response(200, json=[])  # no matching ACL
        return httpx.Response(200, json=[])

    config = gather(_client(handler))
    assert config.blocklist == ()
    assert config.cache_settings == []
    assert config.rate_limiters == []
