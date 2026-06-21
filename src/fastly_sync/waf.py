# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Reconcile an IP blocklist onto a Fastly Edge ACL (WAF blacklisting)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import FastlyAPIError
from .fastly import FastlyClient
from .models import BlockEntry

DEFAULT_ACL_NAME = "waf_blocklist"


def _snippet_name(acl_name: str) -> str:
    return f"{acl_name}-block"


def _block_vcl(acl_name: str) -> str:
    return f'if (client.ip ~ {acl_name}) {{ error 403 "Forbidden"; }}'


@dataclass
class BlocklistResult:
    """The outcome of a blocklist synchronisation run."""

    acl_name: str
    dry_run: bool = False
    added: list[BlockEntry] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def bootstrap_acl(client: FastlyClient, acl_name: str = DEFAULT_ACL_NAME) -> int:
    """Create the ACL and the enforcing VCL snippet on a new active version.

    This is the one-time, versioned setup; afterwards
    :func:`synchronize_blocklist` only mutates the (unversioned) ACL entries.
    Returns the activated version number.
    """
    version = client.clone_version(client.get_active_version())
    client.create_acl(version, acl_name)
    client.upsert_vcl_snippet(version, _snippet_name(acl_name), _block_vcl(acl_name))
    client.activate_version(version)
    return version


def synchronize_blocklist(
    entries: Sequence[BlockEntry],
    client: FastlyClient,
    acl_name: str = DEFAULT_ACL_NAME,
    *,
    dry_run: bool = False,
) -> BlocklistResult:
    """Reconcile ``entries`` against the named Edge ACL.

    Missing entries are added and stale ones removed; the ACL must already
    exist (run :func:`bootstrap_acl` once first).

    Raises:
        FastlyAPIError: if the ACL does not exist on the active version.
    """
    result = BlocklistResult(acl_name=acl_name, dry_run=dry_run)
    acl_id = client.get_acl_id(acl_name)
    if acl_id is None:
        raise FastlyAPIError(
            f"ACL '{acl_name}' not found on the active version; "
            "run with --bootstrap first"
        )

    current = {_current_key(e): e for e in client.list_acl_entries(acl_id)}
    desired = {entry.key: entry for entry in entries}

    result.added = [entry for key, entry in desired.items() if key not in current]
    removed_ids = [
        str(entry["id"]) for key, entry in current.items() if key not in desired
    ]
    result.removed = [
        str(entry["ip"]) for key, entry in current.items() if key not in desired
    ]

    if not dry_run:
        client.update_acl_entries(acl_id, result.added, removed_ids)
    return result


def _current_key(entry: dict[str, Any]) -> tuple[str, int | None]:
    subnet = entry.get("subnet")
    if subnet is None or subnet == "":
        return (str(entry["ip"]), None)
    return (str(entry["ip"]), int(subnet))
