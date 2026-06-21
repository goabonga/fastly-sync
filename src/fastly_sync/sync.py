# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Orchestrate the synchronisation of a desired state onto a Fastly service."""

from __future__ import annotations

from .fastly import FastlyClient
from .models import DesiredState, SyncAction, SyncResult


def synchronize(
    state: DesiredState,
    client: FastlyClient,
    *,
    dry_run: bool = False,
) -> SyncResult:
    """Apply ``state`` to the Fastly service behind ``client``.

    When ``dry_run`` is set, no version is cloned or activated and no
    mutating call is made; the returned result lists the changes that
    *would* be applied.
    """
    result = SyncResult(dry_run=dry_run)
    active = client.get_active_version()
    target = active if dry_run else client.clone_version(active)

    for endpoint in state.endpoints:
        if not dry_run:
            client.upsert_cache_setting(target, endpoint)
        detail = f"cache, ttl={endpoint.ttl}s" if endpoint.action == "cache" else "pass"
        result.applied.append(SyncAction(kind="cdn", name=endpoint.path, detail=detail))

    for rule in state.rate_limiters:
        if not dry_run:
            client.upsert_rate_limiter(target, rule)
        result.applied.append(
            SyncAction(
                kind="ratelimiter",
                name=rule.name,
                detail=f"{rule.limit} req / {rule.window}s",
            )
        )

    if not dry_run:
        client.activate_version(target)

    return result
