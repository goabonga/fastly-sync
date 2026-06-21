# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Immutable value objects describing the desired Fastly state."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CdnEndpoint:
    """A path served through the CDN, with its derived cache policy.

    ``action`` is either ``"cache"`` or ``"pass"``. ``ttl`` is the cache
    lifetime in seconds (ignored when ``action`` is ``"pass"``);
    ``stale_while_revalidate`` and ``stale_if_error`` are the serve-stale
    windows in seconds. ``condition_name`` / ``match_statement`` scope the
    cache setting to this path via a Fastly request condition; ``description``
    is a freeform note stored on that condition's ``comment``.
    """

    path: str
    methods: tuple[str, ...]
    action: str = "pass"
    ttl: int = 0
    stale_while_revalidate: int = 0
    stale_if_error: int = 0
    condition_name: str = ""
    match_statement: str = ""
    description: str = ""


@dataclass(frozen=True)
class RateLimiterRule:
    """A rate limiter derived from an ``x-fastly-ratelimit`` spec extension."""

    name: str
    path: str
    limit: int
    window: int


@dataclass(frozen=True)
class BlockEntry:
    """An IP or CIDR to blacklist in a Fastly Edge ACL.

    ``subnet`` is the CIDR prefix length, or ``None`` for a single host. Two
    entries are reconciled by their ``(ip, subnet)`` identity; ``comment`` is
    metadata only.
    """

    ip: str
    subnet: int | None = None
    comment: str = ""

    @property
    def key(self) -> tuple[str, int | None]:
        """Identity used to reconcile against existing ACL entries."""
        return (self.ip, self.subnet)


@dataclass(frozen=True)
class DesiredState:
    """The full configuration derived from an OpenAPI spec."""

    endpoints: tuple[CdnEndpoint, ...]
    rate_limiters: tuple[RateLimiterRule, ...]


@dataclass(frozen=True)
class SyncAction:
    """A single change produced by a synchronisation run."""

    kind: str
    name: str
    detail: str


@dataclass
class SyncResult:
    """The outcome of a synchronisation run."""

    dry_run: bool = False
    applied: list[SyncAction] = field(default_factory=list)
    removed: list[SyncAction] = field(default_factory=list)
