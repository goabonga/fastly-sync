# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Read the live Fastly configuration managed by fastly-sync."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .fastly import (
    RATELIMIT_NAME_PREFIX,
    FastlyClient,
    managed_rate_limiter_name,
    serve_stale_header_data,
)
from .models import BlockEntry, DesiredState
from .waf import DEFAULT_ACL_NAME, export_blocklist

# Cache settings we manage are scoped to a "cache-…" condition (see spec.py).
_OWNED_CONDITION_PREFIX = "cache-"
_OWNED_HEADER_PREFIX = "serve-stale-"


@dataclass(frozen=True)
class LiveConfig:
    """A snapshot of the managed configuration on the active service version."""

    version: int
    cache_settings: list[dict[str, Any]]
    conditions: list[dict[str, Any]]
    headers: list[dict[str, Any]]
    rate_limiters: list[dict[str, Any]]
    blocklist: tuple[BlockEntry, ...]


def from_desired_state(
    state: DesiredState, blocklist: Sequence[BlockEntry]
) -> LiveConfig:
    """Build a :class:`LiveConfig` from the spec-derived desired state.

    Lets ``sync`` render the *desired* config (terraform / csv) with the same
    renderers used by ``show`` — offline, without contacting Fastly.
    """
    cache_settings = [
        {
            "name": endpoint.path,
            "action": endpoint.action,
            "ttl": endpoint.ttl,
            "stale_ttl": endpoint.stale_if_error,
            "cache_condition": endpoint.condition_name,
            "description": endpoint.description,
            "methods": ",".join(endpoint.methods),
        }
        for endpoint in state.endpoints
    ]
    headers = [
        serve_stale_header_data(endpoint)
        for endpoint in state.endpoints
        if endpoint.action == "cache"
        and (endpoint.stale_while_revalidate or endpoint.stale_if_error)
    ]
    conditions = [
        {
            "name": endpoint.condition_name,
            "statement": endpoint.match_statement,
            "type": "CACHE",
            "priority": 10,
            "comment": endpoint.description,
        }
        for endpoint in state.endpoints
    ]
    rate_limiters = [
        {
            "name": managed_rate_limiter_name(rule.name),
            "http_methods": ",".join(rule.http_methods),
            "rps_limit": rule.limit,
            "window_size": rule.window,
            "penalty_box_duration": rule.penalty_box_duration,
            "action": rule.action,
            "client_key": rule.client_key,
            "logger_type": rule.logger_type,
            "response_object_name": rule.response_object_name,
            "uri_dictionary_name": rule.uri_dictionary_name,
            "feature_revision": rule.feature_revision,
        }
        for rule in state.rate_limiters
    ]
    return LiveConfig(
        version=0,
        cache_settings=cache_settings,
        conditions=conditions,
        headers=headers,
        rate_limiters=rate_limiters,
        blocklist=tuple(blocklist),
    )


def gather(client: FastlyClient, acl_name: str = DEFAULT_ACL_NAME) -> LiveConfig:
    """Collect the CDN, rate limiter and WAF config fastly-sync owns.

    Only objects following our naming convention are reported; manually
    created config is ignored. The blocklist is empty when the ACL is absent.
    """
    version = client.get_active_version()
    conditions = [
        cond
        for cond in client.list_conditions(version)
        if str(cond.get("name", "")).startswith(_OWNED_CONDITION_PREFIX)
    ]
    headers = [
        header
        for header in client.list_headers(version)
        if str(header.get("name", "")).startswith(_OWNED_HEADER_PREFIX)
    ]
    # The cache_settings object has no comment field, so descriptions live on
    # the matching condition's comment (see fastly.upsert_condition).
    comments = {
        str(cond.get("name", "")): str(cond.get("comment", "")) for cond in conditions
    }
    cache_settings = [
        {
            **setting,
            "description": comments.get(str(setting.get("cache_condition", "")), ""),
        }
        for setting in client.list_cache_settings(version)
        if str(setting.get("cache_condition", "")).startswith(_OWNED_CONDITION_PREFIX)
    ]
    rate_limiters = [
        limiter
        for limiter in client.list_rate_limiters(version)
        if str(limiter.get("name", "")).startswith(RATELIMIT_NAME_PREFIX)
    ]
    blocklist: tuple[BlockEntry, ...] = ()
    if client.get_acl_id(acl_name) is not None:
        blocklist = export_blocklist(client, acl_name)
    return LiveConfig(
        version=version,
        cache_settings=cache_settings,
        conditions=conditions,
        headers=headers,
        rate_limiters=rate_limiters,
        blocklist=blocklist,
    )
