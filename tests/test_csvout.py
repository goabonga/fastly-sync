# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

from fastly_sync import csvout
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
            "description": "Public, comma, note",
        }
    ],
    conditions=[],
    rate_limiters=[{"name": "fsync-widgets", "rps_limit": 100, "window_size": 60}],
    blocklist=(
        BlockEntry("198.51.100.7", None, "bad"),
        BlockEntry("203.0.113.0", 24, ""),
    ),
)


def test_render_cdn_csv():
    out = csvout.render_cdn(CONFIG)
    lines = out.splitlines()
    assert lines[0] == "name,action,ttl,stale_ttl,cache_condition,description"
    # A comma in the description is properly quoted.
    assert lines[1] == '/widgets,cache,60,300,cache-widgets,"Public, comma, note"'


def test_render_rate_limiters_csv():
    out = csvout.render_rate_limiters(CONFIG)
    assert out.splitlines() == ["name,rps_limit,window_size", "fsync-widgets,100,60"]


def test_render_waf_csv():
    out = csvout.render_waf(CONFIG)
    assert out.splitlines() == [
        "ip,subnet,comment",
        "198.51.100.7,,bad",
        "203.0.113.0,24,",
    ]


def test_render_all_csv():
    out = csvout.render_all(CONFIG)
    lines = out.splitlines()
    assert lines[0] == "kind,name,detail"
    assert "cdn,/widgets,action=cache;ttl=60" in lines
    assert "ratelimiter,fsync-widgets,100 req/60s" in lines
    assert "waf,203.0.113.0/24," in lines
