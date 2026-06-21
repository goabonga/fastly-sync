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
    def __init__(self, *, conditions=(), cache_settings=(), headers=(), limiters=()):
        self.calls = []
        self._conditions = list(conditions)
        self._cache_settings = list(cache_settings)
        self._headers = list(headers)
        self._limiters = list(limiters)

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

    # listing / deletion (prune)
    def list_conditions(self, version):
        return self._conditions

    def list_cache_settings(self, version):
        return self._cache_settings

    def list_headers(self, version):
        return self._headers

    def list_rate_limiters(self, version):
        return self._limiters

    def delete_condition(self, version, name):
        self.calls.append(("delete_condition", name))

    def delete_cache_setting(self, version, name):
        self.calls.append(("delete_cache_setting", name))

    def delete_header(self, version, name):
        self.calls.append(("delete_header", name))

    def delete_rate_limiter(self, rate_limiter_id):
        self.calls.append(("delete_rate_limiter", rate_limiter_id))


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


PRUNE_STATE = DesiredState(
    endpoints=(
        CdnEndpoint(
            path="/keep",
            methods=("GET",),
            action="cache",
            ttl=60,
            condition_name="cache-keep",
        ),
    ),
    rate_limiters=(RateLimiterRule(name="keep", path="/keep", limit=10, window=60),),
)


def _client_with_orphans():
    return RecordingClient(
        conditions=[
            {"name": "cache-old"},
            {"name": "cache-keep"},
            {"name": "manual"},
        ],
        cache_settings=[
            {"name": "/old", "cache_condition": "cache-old"},
            {"name": "/keep", "cache_condition": "cache-keep"},
            {"name": "/manual", "cache_condition": "other"},
        ],
        headers=[
            {"name": "serve-stale-cache-old"},
            {"name": "manual-header"},
        ],
        limiters=[
            {"id": "r1", "name": "fsync-old"},
            {"id": "r2", "name": "fsync-keep"},
            {"id": "r3", "name": "manual"},
        ],
    )


def test_prune_deletes_only_owned_orphans():
    client = _client_with_orphans()
    result = synchronize(PRUNE_STATE, client)
    removed = {(a.kind, a.name) for a in result.removed}
    assert removed == {
        ("cdn-condition", "cache-old"),
        ("cdn", "/old"),
        ("cdn-header", "serve-stale-cache-old"),
        ("ratelimiter", "fsync-old"),
    }
    assert ("delete_condition", "cache-old") in client.calls
    assert ("delete_cache_setting", "/old") in client.calls
    assert ("delete_header", "serve-stale-cache-old") in client.calls
    assert ("delete_rate_limiter", "r1") in client.calls
    # Manual / still-desired objects are never deleted.
    assert not any(
        c[0].startswith("delete_") and c[1] == "manual" for c in client.calls
    )


def test_prune_dry_run_reports_without_deleting():
    client = _client_with_orphans()
    result = synchronize(PRUNE_STATE, client, dry_run=True)
    assert len(result.removed) == 4
    assert not any(c[0].startswith("delete_") for c in client.calls)


def test_prune_respects_component_scope():
    client = _client_with_orphans()
    result = synchronize(PRUNE_STATE, client, components={Component.CDN})
    kinds = {a.kind for a in result.removed}
    assert "ratelimiter" not in kinds
    assert not any(c[0] == "delete_rate_limiter" for c in client.calls)


def test_no_prune_keeps_orphans():
    client = _client_with_orphans()
    result = synchronize(PRUNE_STATE, client, prune=False)
    assert result.removed == []
    assert not any(c[0].startswith("delete_") for c in client.calls)


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
