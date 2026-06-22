# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

from fastly_sync import terraform
from fastly_sync.models import BlockEntry
from fastly_sync.show import LiveConfig

CONFIG = LiveConfig(
    version=3,
    cache_settings=[
        {
            "name": "/widgets",
            "action": "cache",
            "ttl": 60,
            "stale_ttl": 300,
            "cache_condition": "cache-widgets",
        }
    ],
    conditions=[
        {
            "name": "cache-widgets",
            "statement": 'req.url ~ "^/widgets"',
            "type": "CACHE",
            "priority": 10,
        }
    ],
    headers=[
        {
            "name": "serve-stale-cache-widgets",
            "type": "cache",
            "action": "set",
            "dst": "http.Surrogate-Control",
            "src": "stale-while-revalidate=60",
            "cache_condition": "cache-widgets",
            "priority": 10,
        }
    ],
    rate_limiters=[{"name": "fsync-widgets", "rps_limit": 100, "window_size": 60}],
    blocklist=(
        BlockEntry("198.51.100.7", None, "bad"),
        BlockEntry("203.0.113.0", 24, ""),
    ),
)


def test_render_cdn():
    hcl = terraform.render(CONFIG, "waf_blocklist", cdn=True)
    assert 'resource "fastly_service_vcl" "this"' in hcl
    assert "cache_setting {" in hcl
    assert 'name            = "/widgets"' in hcl
    assert "condition {" in hcl
    # The VCL statement's quotes are escaped for HCL.
    assert r'statement = "req.url ~ \"^/widgets\""' in hcl
    # Serve-stale header block is part of the CDN scope.
    assert "header {" in hcl
    assert 'destination     = "http.Surrogate-Control"' in hcl
    assert "rate_limiter {" not in hcl
    assert "fastly_service_acl_entries" not in hcl


def test_render_rate_limiter():
    hcl = terraform.render(CONFIG, "waf_blocklist", ratelimit=True)
    assert "rate_limiter {" in hcl
    assert 'name                 = "fsync-widgets"' in hcl
    assert "rps_limit            = 100" in hcl
    assert "cache_setting {" not in hcl


def test_render_rate_limiter_all_fields():
    config = LiveConfig(
        version=1,
        cache_settings=[],
        conditions=[],
        headers=[],
        rate_limiters=[
            {
                "name": "fsync-login",
                "http_methods": ["GET", "POST"],
                "rps_limit": 5,
                "window_size": 60,
                "penalty_box_duration": 10,
                "action": "response",
                "client_key": "req.http.X-Key",
                "logger_type": "syslog",
                "response_object_name": "rl-response",
                "uri_dictionary_name": "rl-uris",
                "feature_revision": 2,
                "response": {
                    "status": 429,
                    "content": "Too many",
                    "content_type": "text/plain",
                },
            }
        ],
        blocklist=(),
    )
    hcl = terraform.render(config, "waf_blocklist", ratelimit=True)
    assert 'http_methods         = "GET,POST"' in hcl
    assert "feature_revision     = 2" in hcl
    assert 'logger_type          = "syslog"' in hcl
    assert 'response_object_name = "rl-response"' in hcl
    assert 'uri_dictionary_name  = "rl-uris"' in hcl
    assert "response {" in hcl
    assert "status       = 429" in hcl


def test_render_waf():
    hcl = terraform.render(CONFIG, "waf_blocklist", waf=True)
    assert "acl {" in hcl
    assert 'resource "fastly_service_acl_entries" "waf_blocklist"' in hcl
    assert 'ip      = "198.51.100.7"' in hcl
    assert "subnet  = 24" in hcl  # CIDR entry keeps its subnet
    assert 'comment = "bad"' in hcl


def test_render_all_sections():
    hcl = terraform.render(CONFIG, "waf_blocklist", cdn=True, ratelimit=True, waf=True)
    assert "cache_setting {" in hcl
    assert "rate_limiter {" in hcl
    assert "acl {" in hcl
    assert "fastly_service_acl_entries" in hcl


def test_hcl_escaping():
    config = LiveConfig(
        version=1,
        cache_settings=[],
        conditions=[{"name": "cache-x", "statement": 'a"b\\c', "type": "CACHE"}],
        headers=[],
        rate_limiters=[],
        blocklist=(),
    )
    hcl = terraform.render(config, "waf_blocklist", cdn=True)
    assert r'statement = "a\"b\\c"' in hcl


def test_render_nothing_when_empty():
    config = LiveConfig(
        version=1,
        cache_settings=[],
        conditions=[],
        headers=[],
        rate_limiters=[],
        blocklist=(),
    )
    assert terraform.render(config, "waf_blocklist", cdn=True) == ""
