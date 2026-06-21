# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import pytest

from fastly_sync.errors import FastlyAPIError
from fastly_sync.models import BlockEntry
from fastly_sync.waf import bootstrap_acl, synchronize_blocklist


class FakeClient:
    def __init__(self, acl_id="ACL1", current=None):
        self.acl_id = acl_id
        self.current = current if current is not None else []
        self.calls = []
        self.updated = None

    def get_active_version(self):
        self.calls.append("get_active_version")
        return 1

    def clone_version(self, version):
        self.calls.append(("clone_version", version))
        return 2

    def create_acl(self, version, name):
        self.calls.append(("create_acl", version, name))
        return "ACL2"

    def upsert_vcl_snippet(self, version, name, content):
        self.calls.append(("upsert_vcl_snippet", version, name, content))

    def activate_version(self, version):
        self.calls.append(("activate_version", version))

    def get_acl_id(self, name):
        self.calls.append(("get_acl_id", name))
        return self.acl_id

    def list_acl_entries(self, acl_id):
        self.calls.append(("list_acl_entries", acl_id))
        return self.current

    def update_acl_entries(self, acl_id, additions, removed_ids):
        self.updated = (acl_id, additions, removed_ids)


def test_bootstrap_creates_acl_and_snippet():
    client = FakeClient()
    version = bootstrap_acl(client, "waf_blocklist")
    assert version == 2
    assert ("create_acl", 2, "waf_blocklist") in client.calls
    assert ("activate_version", 2) in client.calls
    snippet = next(c for c in client.calls if c[0] == "upsert_vcl_snippet")
    assert snippet[2] == "waf_blocklist-block"
    assert "client.ip ~ waf_blocklist" in snippet[3]
    assert "error 403" in snippet[3]


def test_synchronize_adds_and_removes():
    client = FakeClient(current=[{"id": "e1", "ip": "10.0.0.1", "subnet": None}])
    result = synchronize_blocklist([BlockEntry("10.0.0.2")], client, "waf_blocklist")
    assert [e.ip for e in result.added] == ["10.0.0.2"]
    assert result.removed == ["10.0.0.1"]
    acl_id, additions, removed_ids = client.updated
    assert acl_id == "ACL1"
    assert [e.ip for e in additions] == ["10.0.0.2"]
    assert removed_ids == ["e1"]


def test_dry_run_does_not_update():
    client = FakeClient(current=[{"id": "e1", "ip": "10.0.0.1", "subnet": None}])
    result = synchronize_blocklist([BlockEntry("10.0.0.2")], client, dry_run=True)
    assert result.dry_run is True
    assert client.updated is None
    assert [e.ip for e in result.added] == ["10.0.0.2"]
    assert result.removed == ["10.0.0.1"]


def test_string_subnet_matches_int_subnet():
    client = FakeClient(current=[{"id": "e1", "ip": "203.0.113.0", "subnet": "24"}])
    result = synchronize_blocklist([BlockEntry("203.0.113.0", 24)], client)
    assert result.added == []
    assert result.removed == []
    # update is still called with empty ops; the client decides to no-op.
    assert client.updated == ("ACL1", [], [])


def test_missing_acl_raises():
    client = FakeClient(acl_id=None)
    with pytest.raises(FastlyAPIError, match="not found"):
        synchronize_blocklist([BlockEntry("10.0.0.1")], client)
