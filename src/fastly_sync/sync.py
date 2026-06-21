# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Orchestrate the synchronisation of a desired state onto a Fastly service."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum

from .fastly import RATELIMIT_NAME_PREFIX, FastlyClient, managed_rate_limiter_name
from .models import DesiredState, SyncAction, SyncResult

_CONDITION_PREFIX = "cache-"
_HEADER_PREFIX = "serve-stale-"


class Component(StrEnum):
    """Selectable parts of the OpenAPI-derived desired state."""

    CDN = "cdn"
    RATELIMIT = "ratelimit"


ALL_COMPONENTS: frozenset[Component] = frozenset(Component)


def resolve_components(
    only: Component | None = None, skip: Component | None = None
) -> frozenset[Component]:
    """Return the set of components in scope for a run (for sync and prune)."""
    components = set(ALL_COMPONENTS)
    if only is not None:
        components = {only}
    if skip is not None:
        components.discard(skip)
    return frozenset(components)


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
    components = resolve_components(only, skip)
    return DesiredState(
        endpoints=state.endpoints if Component.CDN in components else (),
        rate_limiters=(
            state.rate_limiters if Component.RATELIMIT in components else ()
        ),
    )


def synchronize(
    state: DesiredState,
    client: FastlyClient,
    *,
    components: Collection[Component] = ALL_COMPONENTS,
    prune: bool = True,
    dry_run: bool = False,
) -> SyncResult:
    """Apply ``state`` to the Fastly service behind ``client``.

    When ``prune`` is set (the default), managed objects in scope that are no
    longer described by ``state`` are deleted, so the spec stays the source of
    truth. ``components`` bounds both the upserts and the prune so a scoped run
    (``--only``/``--skip``) never touches the component it excluded.

    When ``dry_run`` is set, no version is cloned or activated and no mutating
    call is made; ``result.applied`` / ``result.removed`` list what *would*
    change.
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

    if prune:
        _prune(client, target, state, components, result, dry_run=dry_run)

    if not dry_run:
        client.activate_version(target)

    return result


def _prune(
    client: FastlyClient,
    version: int,
    state: DesiredState,
    components: Collection[Component],
    result: SyncResult,
    *,
    dry_run: bool,
) -> None:
    """Delete managed objects in scope that ``state`` no longer describes.

    Only objects following our naming convention are ever removed, so config
    created outside fastly-sync is left untouched.
    """
    if Component.CDN in components:
        _prune_cdn(client, version, state, result, dry_run=dry_run)
    if Component.RATELIMIT in components:
        _prune_rate_limiters(client, version, state, result, dry_run=dry_run)


def _prune_cdn(
    client: FastlyClient,
    version: int,
    state: DesiredState,
    result: SyncResult,
    *,
    dry_run: bool,
) -> None:
    desired_conditions = {endpoint.condition_name for endpoint in state.endpoints}
    for condition in client.list_conditions(version):
        name = str(condition.get("name", ""))
        if name.startswith(_CONDITION_PREFIX) and name not in desired_conditions:
            if not dry_run:
                client.delete_condition(version, name)
            result.removed.append(SyncAction("cdn-condition", name, "pruned"))

    desired_settings = {endpoint.path for endpoint in state.endpoints}
    for setting in client.list_cache_settings(version):
        name = str(setting.get("name", ""))
        owned = str(setting.get("cache_condition", "")).startswith(_CONDITION_PREFIX)
        if owned and name not in desired_settings:
            if not dry_run:
                client.delete_cache_setting(version, name)
            result.removed.append(SyncAction("cdn", name, "pruned"))

    desired_headers = {
        f"{_HEADER_PREFIX}{endpoint.condition_name}"
        for endpoint in state.endpoints
        if endpoint.action == "cache"
        and (endpoint.stale_while_revalidate or endpoint.stale_if_error)
    }
    for header in client.list_headers(version):
        name = str(header.get("name", ""))
        if name.startswith(_HEADER_PREFIX) and name not in desired_headers:
            if not dry_run:
                client.delete_header(version, name)
            result.removed.append(SyncAction("cdn-header", name, "pruned"))


def _prune_rate_limiters(
    client: FastlyClient,
    version: int,
    state: DesiredState,
    result: SyncResult,
    *,
    dry_run: bool,
) -> None:
    desired = {managed_rate_limiter_name(rule.name) for rule in state.rate_limiters}
    for limiter in client.list_rate_limiters(version):
        name = str(limiter.get("name", ""))
        if name.startswith(RATELIMIT_NAME_PREFIX) and name not in desired:
            if not dry_run:
                client.delete_rate_limiter(str(limiter["id"]))
            result.removed.append(SyncAction("ratelimiter", name, "pruned"))
