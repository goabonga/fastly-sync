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
    rate_limiters=[
        {
            "name": "fsync-widgets",
            "http_methods": "GET,POST",
            "rps_limit": 100,
            "window_size": 60,
            "penalty_box_duration": 1,
            "action": "response",
            "client_key": "req.http.Fastly-Client-IP",
            "feature_revision": 1,
        }
    ],
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
    lines = csvout.render_rate_limiters(CONFIG).splitlines()
    assert lines[0] == (
        "name,http_methods,rps_limit,window_size,penalty_box_duration,"
        "action,client_key,logger_type,response_object_name,"
        "uri_dictionary_name,feature_revision"
    )
    assert lines[1].startswith(
        'fsync-widgets,"GET,POST",100,60,1,response,req.http.Fastly-Client-IP'
    )


def test_render_rate_limiters_csv_list_http_methods():
    config = LiveConfig(
        version=1,
        cache_settings=[],
        conditions=[],
        rate_limiters=[{"name": "fsync-x", "http_methods": ["GET", "HEAD"]}],
        blocklist=(),
    )
    lines = csvout.render_rate_limiters(config).splitlines()
    assert lines[1].startswith('fsync-x,"GET,HEAD"')


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
