# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import json

import httpx
import pytest

from fastly_sync import spec as spec_module
from fastly_sync.errors import SpecError
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
    with pytest.raises(SpecError, match="cannot read"):
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
        pytest.raises(SpecError, match="cannot fetch"),
    ):
        load_spec("https://example.test/openapi.json", client=client)


def test_load_remote_creates_and_closes_own_client(monkeypatch):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=json.dumps(SAMPLE))
    )
    owned = httpx.Client(transport=transport)
    monkeypatch.setattr(spec_module.httpx, "Client", lambda **kwargs: owned)
    data = load_spec("http://example.test/openapi.json")
    assert data["openapi"] == "3.0.0"
    assert owned.is_closed


def test_build_desired_state():
    state = build_desired_state(SAMPLE)
    paths = {endpoint.path: endpoint.methods for endpoint in state.endpoints}
    assert paths["/widgets"] == ("GET", "POST")
    assert paths["/health"] == ("GET",)
    assert "/legacy" not in paths
    assert len(state.rate_limiters) == 1
    rule = state.rate_limiters[0]
    assert rule.name == "widgets"
    assert rule.limit == 100
    assert rule.window == 30


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


def test_rate_limit_slug_falls_back_to_root():
    state = build_desired_state({"paths": {"/": {"x-fastly-ratelimit": {"limit": 5}}}})
    assert state.rate_limiters[0].name == "root"


def test_invalid_rate_limit_raises():
    with pytest.raises(SpecError, match="x-fastly-ratelimit"):
        build_desired_state({"paths": {"/x": {"x-fastly-ratelimit": {"window": 10}}}})


def test_non_dict_rate_limit_is_ignored():
    state = build_desired_state({"paths": {"/x": {"get": {}, "x-fastly-ratelimit": 5}}})
    assert state.rate_limiters == ()
