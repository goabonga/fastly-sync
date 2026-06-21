# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

from fastly_sync.models import CdnEndpoint, DesiredState, RateLimiterRule
from fastly_sync.sync import Component, select_state, synchronize

STATE = DesiredState(
    endpoints=(
        CdnEndpoint(
            path="/w",
            methods=("GET",),
            action="cache",
            ttl=3600,
            condition_name="cache-w",
        ),
        CdnEndpoint(path="/w", methods=("GET", "POST"), action="pass"),
    ),
    rate_limiters=(RateLimiterRule(name="w", path="/w", limit=100, window=60),),
)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get_active_version(self):
        self.calls.append(("get_active_version",))
        return 1

    def clone_version(self, version):
        self.calls.append(("clone_version", version))
        return 2

    def upsert_condition(self, version, endpoint):
        self.calls.append(("upsert_condition", version, endpoint.condition_name))

    def upsert_cache_setting(self, version, endpoint):
        self.calls.append(("upsert_cache_setting", version, endpoint.path))

    def upsert_serve_stale_header(self, version, endpoint):
        self.calls.append(("upsert_serve_stale_header", version, endpoint.path))

    def upsert_rate_limiter(self, version, rule):
        self.calls.append(("upsert_rate_limiter", version, rule.name))

    def activate_version(self, version):
        self.calls.append(("activate_version", version))


def test_apply_clones_mutates_and_activates():
    client = RecordingClient()
    result = synchronize(STATE, client)
    assert result.dry_run is False
    assert len(result.applied) == 3
    cdn_details = [a.detail for a in result.applied if a.kind == "cdn"]
    assert cdn_details == ["cache, ttl=3600s", "pass"]
    assert ("clone_version", 1) in client.calls
    assert ("upsert_condition", 2, "cache-w") in client.calls
    assert ("upsert_cache_setting", 2, "/w") in client.calls
    assert ("upsert_rate_limiter", 2, "w") in client.calls
    assert ("activate_version", 2) in client.calls
    # No serve-stale header: the cache endpoint has no stale windows.
    assert not any(call[0] == "upsert_serve_stale_header" for call in client.calls)


def test_serve_stale_header_emitted_for_stale_cache():
    state = DesiredState(
        endpoints=(
            CdnEndpoint(
                path="/w",
                methods=("GET",),
                action="cache",
                ttl=3600,
                stale_while_revalidate=60,
                condition_name="cache-w",
            ),
        ),
        rate_limiters=(),
    )
    client = RecordingClient()
    synchronize(state, client)
    assert ("upsert_serve_stale_header", 2, "/w") in client.calls


def test_select_state_default_keeps_everything():
    selected = select_state(STATE)
    assert selected == STATE


def test_select_state_only_cdn():
    selected = select_state(STATE, only=Component.CDN)
    assert selected.endpoints == STATE.endpoints
    assert selected.rate_limiters == ()


def test_select_state_only_ratelimit():
    selected = select_state(STATE, only=Component.RATELIMIT)
    assert selected.endpoints == ()
    assert selected.rate_limiters == STATE.rate_limiters


def test_select_state_skip_cdn():
    selected = select_state(STATE, skip=Component.CDN)
    assert selected.endpoints == ()
    assert selected.rate_limiters == STATE.rate_limiters


def test_select_state_skip_ratelimit():
    selected = select_state(STATE, skip=Component.RATELIMIT)
    assert selected.endpoints == STATE.endpoints
    assert selected.rate_limiters == ()


def test_dry_run_makes_no_mutating_calls():
    client = RecordingClient()
    result = synchronize(STATE, client, dry_run=True)
    assert result.dry_run is True
    assert len(result.applied) == 3
    assert client.calls == [("get_active_version",)]
