# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Render the live (or desired) Fastly config as CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from .show import LiveConfig


def _csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def render_cdn(config: LiveConfig) -> str:
    rows = [
        [
            setting.get("name", ""),
            setting.get("methods", ""),
            setting.get("action", ""),
            setting.get("ttl", ""),
            setting.get("stale_ttl", ""),
            setting.get("cache_condition", ""),
            setting.get("description", ""),
        ]
        for setting in config.cache_settings
    ]
    return _csv(
        [
            "name",
            "methods",
            "action",
            "ttl",
            "stale_ttl",
            "cache_condition",
            "description",
        ],
        rows,
    )


_RATE_LIMITER_COLUMNS = [
    "name",
    "http_methods",
    "rps_limit",
    "window_size",
    "penalty_box_duration",
    "action",
    "client_key",
    "logger_type",
    "response_object_name",
    "uri_dictionary_name",
    "feature_revision",
]


def _http_methods(value: object) -> str:
    if isinstance(value, list):
        return ",".join(str(method) for method in value)
    return str(value or "")


def render_rate_limiters(config: LiveConfig) -> str:
    rows = []
    for limiter in config.rate_limiters:
        row = [limiter.get(column, "") for column in _RATE_LIMITER_COLUMNS]
        row[1] = _http_methods(limiter.get("http_methods"))
        rows.append(row)
    return _csv(_RATE_LIMITER_COLUMNS, rows)


def render_waf(config: LiveConfig) -> str:
    rows = [
        [
            entry.ip,
            "" if entry.subnet is None else entry.subnet,
            str(entry.negated).lower(),
            entry.comment,
        ]
        for entry in config.blocklist
    ]
    return _csv(["ip", "subnet", "negated", "comment"], rows)


def render_all(config: LiveConfig) -> str:
    rows: list[Sequence[object]] = []
    for setting in config.cache_settings:
        rows.append(
            [
                "cdn",
                setting.get("name", ""),
                f"action={setting.get('action')};ttl={setting.get('ttl')}",
            ]
        )
    for limiter in config.rate_limiters:
        rows.append(
            [
                "ratelimiter",
                limiter.get("name", ""),
                f"{limiter.get('rps_limit')} req/{limiter.get('window_size')}s",
            ]
        )
    for entry in config.blocklist:
        cidr = entry.ip if entry.subnet is None else f"{entry.ip}/{entry.subnet}"
        rows.append(["waf", cidr, entry.comment])
    return _csv(["kind", "name", "detail"], rows)
