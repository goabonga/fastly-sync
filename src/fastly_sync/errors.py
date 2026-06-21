# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Exception hierarchy for fastly-sync."""

from __future__ import annotations


class FastlySyncError(Exception):
    """Base class for every error raised by fastly-sync."""


class SpecError(FastlySyncError):
    """Raised when an OpenAPI spec cannot be loaded or parsed."""


class ConfigError(FastlySyncError):
    """Raised when required configuration (token, service id) is missing."""


class FastlyAPIError(FastlySyncError):
    """Raised when the Fastly API returns an error response."""
