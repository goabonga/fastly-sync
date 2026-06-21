# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

from fastly_sync.models import CdnEndpoint, DesiredState, RateLimiterRule
from fastly_sync.sync import synchronize

STATE = DesiredState(
    endpoints=(
        CdnEndpoint(path="/w", methods=("GET",), action="cache", ttl=3600),
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

    def upsert_cache_setting(self, version, endpoint):
        self.calls.append(("upsert_cache_setting", version, endpoint.path))

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
    assert ("upsert_cache_setting", 2, "/w") in client.calls
    assert ("upsert_rate_limiter", 2, "w") in client.calls
    assert ("activate_version", 2) in client.calls


def test_dry_run_makes_no_mutating_calls():
    client = RecordingClient()
    result = synchronize(STATE, client, dry_run=True)
    assert result.dry_run is True
    assert len(result.applied) == 3
    assert client.calls == [("get_active_version",)]
