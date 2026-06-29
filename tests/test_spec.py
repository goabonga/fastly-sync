# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import json

import httpx
import pytest

from fastly_sync import loader as loader_module
from fastly_sync.errors import SourceError, SpecError
from fastly_sync.spec import build_desired_state, load_spec

SAMPLE = {
    "openapi": "3.0.0",
    "paths": {
        "/widgets": {
            "get": {},
            "post": {},
            "x-fastly-ratelimit": {"limit": 100, "window": 30, "name": "widgets"},
        },
        "/health": {"get": {}},
        "/legacy": "not-a-dict",
    },
}


def test_load_local_spec(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(SAMPLE), encoding="utf-8")
    assert load_spec(str(path))["openapi"] == "3.0.0"


def test_load_local_missing_file(tmp_path):
    with pytest.raises(SourceError, match="cannot read"):
        load_spec(str(tmp_path / "absent.json"))


def test_load_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SpecError, match="invalid JSON"):
        load_spec(str(path))


def test_load_non_object_json(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SpecError, match="must be a JSON object"):
        load_spec(str(path))


def test_load_remote_with_injected_client():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=json.dumps(SAMPLE))
    )
    with httpx.Client(transport=transport) as client:
        data = load_spec("https://example.test/openapi.json", client=client)
    assert data["openapi"] == "3.0.0"


def test_load_remote_http_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(SourceError, match="cannot fetch"),
    ):
        load_spec("https://example.test/openapi.json", client=client)


def test_load_remote_creates_and_closes_own_client(monkeypatch):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=json.dumps(SAMPLE))
    )
    owned = httpx.Client(transport=transport)
    monkeypatch.setattr(loader_module.httpx, "Client", lambda **kwargs: owned)
    data = load_spec("http://example.test/openapi.json")
    assert data["openapi"] == "3.0.0"
    assert owned.is_closed


def test_build_desired_state():
    state = build_desired_state(SAMPLE)
    endpoints = {endpoint.path: endpoint for endpoint in state.endpoints}
    assert endpoints["/widgets"].methods == ("GET", "POST")
    assert endpoints["/health"].methods == ("GET",)
    assert "/legacy" not in endpoints
    # /widgets exposes POST -> not cacheable -> pass; /health is GET-only.
    assert endpoints["/widgets"].action == "pass"
    assert endpoints["/widgets"].ttl == 0
    assert endpoints["/health"].action == "cache"
    assert endpoints["/health"].ttl == 3600
    assert len(state.rate_limiters) == 1
    rule = state.rate_limiters[0]
    assert rule.name == "widgets"
    assert rule.limit == 100
    assert rule.window == 30


def test_cache_extension_overrides_defaults():
    state = build_desired_state(
        {
            "paths": {
                "/w": {
                    "get": {},
                    "x-fastly-cache": {
                        "ttl": 120,
                        "stale_while_revalidate": 30,
                        "stale_if_error": 90,
                    },
                }
            }
        }
    )
    endpoint = state.endpoints[0]
    assert endpoint.action == "cache"
    assert endpoint.ttl == 120
    assert endpoint.stale_while_revalidate == 30
    assert endpoint.stale_if_error == 90


def test_endpoint_derives_request_condition():
    state = build_desired_state(
        {"paths": {"/widgets/{id}": {"get": {}}, "/health": {"get": {}}}}
    )
    by_path = {endpoint.path: endpoint for endpoint in state.endpoints}
    widgets = by_path["/widgets/{id}"]
    assert widgets.condition_name == "cache-widgets-id"
    # Path parameter becomes a single non-slash segment, both ends anchored.
    assert widgets.match_statement == r'req.url ~ "^/widgets/[^/]+(?:\?|$)"'
    assert by_path["/health"].match_statement == r'req.url ~ "^/health(?:\?|$)"'


def test_sibling_paths_do_not_overlap():
    state = build_desired_state(
        {"paths": {"/widget": {"get": {}}, "/widgets": {"get": {}}}}
    )
    statements = {e.path: e.match_statement for e in state.endpoints}
    # "^/widget(?:\?|$)" must not match "/widgets" — the end anchor prevents it.
    assert statements["/widget"] == r'req.url ~ "^/widget(?:\?|$)"'
    assert statements["/widgets"] == r'req.url ~ "^/widgets(?:\?|$)"'


def test_cache_extension_description():
    state = build_desired_state(
        {"paths": {"/w": {"get": {}, "x-fastly-cache": {"description": "Public API"}}}}
    )
    assert state.endpoints[0].description == "Public API"
    # No extension -> empty description.
    plain = build_desired_state({"paths": {"/h": {"get": {}}}})
    assert plain.endpoints[0].description == ""


def test_cache_extension_can_force_pass_on_read_endpoint():
    state = build_desired_state(
        {"paths": {"/w": {"get": {}, "x-fastly-cache": {"action": "pass"}}}}
    )
    endpoint = state.endpoints[0]
    assert endpoint.action == "pass"
    assert endpoint.ttl == 0


def test_invalid_cache_action_raises():
    with pytest.raises(SpecError, match="x-fastly-cache action"):
        build_desired_state(
            {"paths": {"/w": {"get": {}, "x-fastly-cache": {"action": "nope"}}}}
        )


def test_invalid_cache_ttl_raises():
    with pytest.raises(SpecError, match="x-fastly-cache"):
        build_desired_state(
            {"paths": {"/w": {"get": {}, "x-fastly-cache": {"ttl": "soon"}}}}
        )


def test_non_dict_cache_extension_raises():
    with pytest.raises(SpecError, match="expected an object"):
        build_desired_state({"paths": {"/w": {"get": {}, "x-fastly-cache": 5}}})


def test_custom_extension_keys():
    spec = {
        "paths": {
            "/w": {
                "get": {},
                "x-cdn": {"ttl": 99, "description": "custom"},
                "x-rl": {"name": "w", "limit": 7},
                "x-fastly-cache": {"ttl": 1},  # ignored: not the configured key
            }
        }
    }
    state = build_desired_state(spec, cache_key="x-cdn", ratelimit_key="x-rl")
    assert state.endpoints[0].ttl == 99
    assert state.endpoints[0].description == "custom"
    assert state.rate_limiters[0].limit == 7
    # The default keys are not read when custom ones are configured.
    assert state.rate_limiters[0].name == "w"


def test_custom_cache_key_in_error_message():
    with pytest.raises(SpecError, match="invalid x-cdn for"):
        build_desired_state(
            {"paths": {"/w": {"get": {}, "x-cdn": 5}}}, cache_key="x-cdn"
        )


def test_build_desired_state_requires_paths():
    with pytest.raises(SpecError, match="no 'paths'"):
        build_desired_state({"openapi": "3.0.0"})


def test_rate_limit_default_window_and_slug_name():
    state = build_desired_state(
        {"paths": {"/a/b": {"x-fastly-ratelimit": {"limit": 5}}}}
    )
    rule = state.rate_limiters[0]
    assert rule.window == 60
    assert rule.name == "a-b"


def test_rate_limiter_full_fields():
    state = build_desired_state(
        {
            "paths": {
                "/x": {
                    "post": {},
                    "x-fastly-ratelimit": {
                        "limit": 5,
                        "http_methods": "get, post",
                        "action": "log_only",
                        "penalty_box_duration": 30,
                        "client_key": "req.http.X",
                        "logger_type": "syslog",
                        "response_object_name": "r",
                        "uri_dictionary_name": "d",
                        "feature_revision": 3,
                    },
                }
            }
        }
    )
    rule = state.rate_limiters[0]
    assert rule.http_methods == ("GET", "POST")  # comma string, upper-cased
    assert rule.action == "log_only"
    assert rule.penalty_box_duration == 30
    assert rule.client_key == "req.http.X"
    assert rule.logger_type == "syslog"
    assert rule.response_object_name == "r"
    assert rule.uri_dictionary_name == "d"
    assert rule.feature_revision == 3


def test_rate_limiter_http_methods_list_and_default():
    listed = build_desired_state(
        {
            "paths": {
                "/x": {
                    "get": {},
                    "x-fastly-ratelimit": {"limit": 5, "http_methods": ["patch"]},
                }
            }
        }
    )
    assert listed.rate_limiters[0].http_methods == ("PATCH",)
    # Default to the path's methods when http_methods is omitted.
    derived = build_desired_state(
        {"paths": {"/y": {"get": {}, "post": {}, "x-fastly-ratelimit": {"limit": 5}}}}
    )
    assert derived.rate_limiters[0].http_methods == ("GET", "POST")


def test_rate_limit_slug_falls_back_to_root():
    state = build_desired_state({"paths": {"/": {"x-fastly-ratelimit": {"limit": 5}}}})
    assert state.rate_limiters[0].name == "root"


def test_invalid_rate_limit_raises():
    with pytest.raises(SpecError, match="x-fastly-ratelimit"):
        build_desired_state({"paths": {"/x": {"x-fastly-ratelimit": {"window": 10}}}})


def test_non_dict_rate_limit_is_ignored():
    state = build_desired_state({"paths": {"/x": {"get": {}, "x-fastly-ratelimit": 5}}})
    assert state.rate_limiters == ()
