# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Orchestrate the synchronisation of a desired state onto a Fastly service."""

from __future__ import annotations

from enum import StrEnum

from .fastly import FastlyClient
from .models import DesiredState, SyncAction, SyncResult


class Component(StrEnum):
    """Selectable parts of the OpenAPI-derived desired state."""

    CDN = "cdn"
    RATELIMIT = "ratelimit"


def select_state(
    state: DesiredState,
    *,
    only: Component | None = None,
    skip: Component | None = None,
) -> DesiredState:
    """Return ``state`` narrowed to the requested components.

    ``only`` keeps a single component; ``skip`` drops one. They are independent
    here — the CLI rejects passing both at once.
    """
    include_cdn = True
    include_ratelimit = True
    if only is not None:
        include_cdn = only is Component.CDN
        include_ratelimit = only is Component.RATELIMIT
    if skip is Component.CDN:
        include_cdn = False
    if skip is Component.RATELIMIT:
        include_ratelimit = False
    return DesiredState(
        endpoints=state.endpoints if include_cdn else (),
        rate_limiters=state.rate_limiters if include_ratelimit else (),
    )


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
            client.upsert_condition(target, endpoint)
            client.upsert_cache_setting(target, endpoint)
            if endpoint.action == "cache" and (
                endpoint.stale_while_revalidate or endpoint.stale_if_error
            ):
                client.upsert_serve_stale_header(target, endpoint)
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
