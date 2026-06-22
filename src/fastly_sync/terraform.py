# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Render the live Fastly config as Fastly Terraform provider resources.

The output is a *scaffold*: ``fastly_service_vcl`` also requires ``name``,
``domain`` and ``backend`` which fastly-sync does not manage, so those are left
as TODO placeholders. The ``fastly_service_acl_entries`` resource is complete.
"""

from __future__ import annotations

from typing import Any

from .models import BlockEntry
from .show import LiveConfig


def _hcl(value: object) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _condition_block(condition: dict[str, Any]) -> str:
    return (
        "  condition {\n"
        f"    name      = {_hcl(condition.get('name', ''))}\n"
        f"    statement = {_hcl(condition.get('statement', ''))}\n"
        f"    type      = {_hcl(condition.get('type', 'CACHE'))}\n"
        f"    priority  = {int(condition.get('priority', 10))}\n"
        "  }\n"
    )


def _cache_setting_block(setting: dict[str, Any]) -> str:
    return (
        "  cache_setting {\n"
        f"    name            = {_hcl(setting.get('name', ''))}\n"
        f"    action          = {_hcl(setting.get('action', 'pass'))}\n"
        f"    ttl             = {int(setting.get('ttl', 0))}\n"
        f"    stale_ttl       = {int(setting.get('stale_ttl', 0))}\n"
        f"    cache_condition = {_hcl(setting.get('cache_condition', ''))}\n"
        "  }\n"
    )


def _http_methods_hcl(value: object) -> str:
    # Accept a comma string (our model) or a list (live API) -> comma string.
    if isinstance(value, list):
        value = ",".join(str(method) for method in value)
    return _hcl(value or "GET")


def _rate_limiter_block(limiter: dict[str, Any]) -> str:
    lines = [
        "  rate_limiter {\n",
        f"    name                 = {_hcl(limiter.get('name', ''))}\n",
        f"    http_methods         = {_http_methods_hcl(limiter.get('http_methods'))}\n",
        f"    rps_limit            = {int(limiter.get('rps_limit', 0))}\n",
        f"    window_size          = {int(limiter.get('window_size', 60))}\n",
        f"    penalty_box_duration = {int(limiter.get('penalty_box_duration', 1))}\n",
        f"    action               = {_hcl(limiter.get('action', 'response'))}\n",
        f"    client_key           = {_hcl(limiter.get('client_key', 'req.http.Fastly-Client-IP'))}\n",
        f"    feature_revision     = {int(limiter.get('feature_revision', 1))}\n",
    ]
    for key in ("logger_type", "response_object_name", "uri_dictionary_name"):
        value = limiter.get(key)
        if value:
            lines.append(f"    {key:<20} = {_hcl(value)}\n")
    response = limiter.get("response")
    if isinstance(response, dict):
        lines.append(
            "    response {\n"
            f"      status       = {int(response.get('status', 429))}\n"
            f"      content_type = {_hcl(response.get('content_type', 'text/plain'))}\n"
            f"      content      = {_hcl(response.get('content', ''))}\n"
            "    }\n"
        )
    lines.append("  }\n")
    return "".join(lines)


def _acl_block(acl_name: str) -> str:
    return f"  acl {{\n    name = {_hcl(acl_name)}\n  }}\n"


def _entry_block(entry: BlockEntry) -> str:
    lines = ["  entry {\n", f"    ip      = {_hcl(entry.ip)}\n"]
    if entry.subnet is not None:
        lines.append(f"    subnet  = {entry.subnet}\n")
    lines.append(f"    comment = {_hcl(entry.comment)}\n")
    lines.append("  }\n")
    return "".join(lines)


def render(
    config: LiveConfig,
    acl_name: str,
    *,
    cdn: bool = False,
    ratelimit: bool = False,
    waf: bool = False,
) -> str:
    """Render the requested sections as Fastly Terraform resources."""
    inner: list[str] = []
    if cdn:
        inner += [_condition_block(c) for c in config.conditions]
        inner += [_cache_setting_block(s) for s in config.cache_settings]
    if ratelimit:
        inner += [_rate_limiter_block(r) for r in config.rate_limiters]
    if waf:
        inner.append(_acl_block(acl_name))

    parts: list[str] = []
    if inner:
        parts.append(
            'resource "fastly_service_vcl" "this" {\n'
            '  name = "TODO: service name"\n'
            "  # TODO: add domain { ... } and backend { ... } blocks.\n\n"
            + "\n".join(inner)
            + "}\n"
        )
    if waf:
        entries = "\n".join(_entry_block(entry) for entry in config.blocklist)
        parts.append(
            f'resource "fastly_service_acl_entries" {_hcl(acl_name)} {{\n'
            "  service_id = fastly_service_vcl.this.id\n"
            "  acl_id     = fastly_service_vcl.this.acl[0].acl_id\n\n"
            f"{entries}"
            "}\n"
        )
    return "\n".join(parts)
