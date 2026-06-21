# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Read the live Fastly configuration managed by fastly-sync."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .fastly import RATELIMIT_NAME_PREFIX, FastlyClient
from .models import BlockEntry
from .waf import DEFAULT_ACL_NAME, export_blocklist

# Cache settings we manage are scoped to a "cache-…" condition (see spec.py).
_OWNED_CONDITION_PREFIX = "cache-"


@dataclass(frozen=True)
class LiveConfig:
    """A snapshot of the managed configuration on the active service version."""

    version: int
    cache_settings: list[dict[str, Any]]
    rate_limiters: list[dict[str, Any]]
    blocklist: tuple[BlockEntry, ...]


def gather(client: FastlyClient, acl_name: str = DEFAULT_ACL_NAME) -> LiveConfig:
    """Collect the CDN, rate limiter and WAF config fastly-sync owns.

    Only objects following our naming convention are reported; manually
    created config is ignored. The blocklist is empty when the ACL is absent.
    """
    version = client.get_active_version()
    # The cache_settings object has no comment field, so descriptions live on
    # the matching condition's comment (see fastly.upsert_condition).
    comments = {
        str(cond.get("name", "")): str(cond.get("comment", ""))
        for cond in client.list_conditions(version)
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
        rate_limiters=rate_limiters,
        blocklist=blocklist,
    )
