# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Load and validate an IP blocklist from a local or remote text file."""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

import httpx

from .errors import BlocklistError
from .loader import read_source
from .models import BlockEntry


def load_blocklist(
    source: str, *, client: httpx.Client | None = None
) -> tuple[BlockEntry, ...]:
    """Load a blocklist of IPs/CIDRs (one per line, ``#`` for comments).

    The source is a filesystem path or an ``http(s)`` URL. Each entry is
    validated with :mod:`ipaddress`; duplicates are collapsed (last comment
    wins) and the result is sorted for deterministic output.

    Raises:
        SourceError: if the source cannot be read or fetched.
        BlocklistError: if a line is not a valid IP address or CIDR.
    """
    text = read_source(source, client=client)
    entries: dict[tuple[str, int | None], BlockEntry] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        value, _, comment = raw.partition("#")
        token = value.strip()
        if not token:
            continue
        entry = _parse_entry(token, comment.strip(), source, lineno)
        entries[entry.key] = entry
    return tuple(sorted(entries.values(), key=lambda e: (e.ip, e.subnet or 0)))


def dump_blocklist(entries: Sequence[BlockEntry]) -> str:
    """Render block entries back to the text blocklist format.

    The output round-trips through :func:`load_blocklist`: one ``ip`` or
    ``ip/subnet`` per line, with an optional ``# comment`` suffix.
    """
    lines = []
    for entry in entries:
        cidr = entry.ip if entry.subnet is None else f"{entry.ip}/{entry.subnet}"
        prefix = "!" if entry.negated else ""
        head = f"{prefix}{cidr}"
        lines.append(f"{head}  # {entry.comment}" if entry.comment else head)
    return "".join(f"{line}\n" for line in lines)


def _parse_entry(token: str, comment: str, source: str, lineno: int) -> BlockEntry:
    # A leading "!" marks a negated (allowlist) entry.
    negated = token.startswith("!")
    if negated:
        token = token[1:].strip()
    try:
        network = ipaddress.ip_network(token, strict=False)
    except ValueError as exc:
        raise BlocklistError(
            f"{source}:{lineno}: invalid IP/CIDR '{token}': {exc}"
        ) from exc
    is_host = network.prefixlen == network.max_prefixlen
    subnet = None if is_host else network.prefixlen
    return BlockEntry(
        ip=str(network.network_address),
        subnet=subnet,
        comment=comment,
        negated=negated,
    )
