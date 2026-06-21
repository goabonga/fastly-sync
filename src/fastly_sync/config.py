# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Runtime configuration resolved from CLI flags and environment variables."""

from __future__ import annotations

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError

TOKEN_ENV = "FASTLY_API_TOKEN"  # nosec B105 - env var name, not a secret
SERVICE_ID_ENV = "FASTLY_SERVICE_ID"

_ENV_NAMES = {
    "token": TOKEN_ENV,
    "service_id": SERVICE_ID_ENV,
    TOKEN_ENV: TOKEN_ENV,
    SERVICE_ID_ENV: SERVICE_ID_ENV,
}


class Settings(BaseSettings):
    """Credentials required to talk to the Fastly API.

    Values are read from the ``FASTLY_API_TOKEN`` and ``FASTLY_SERVICE_ID``
    environment variables; explicit values passed to :func:`load_settings`
    take precedence.
    """

    model_config = SettingsConfigDict(extra="ignore")

    token: str = Field(min_length=1, validation_alias=TOKEN_ENV)
    service_id: str = Field(min_length=1, validation_alias=SERVICE_ID_ENV)


def load_settings(
    token: str | None = None,
    service_id: str | None = None,
) -> Settings:
    """Resolve settings from explicit values, falling back to the environment.

    Args:
        token: Fastly API token; falls back to ``FASTLY_API_TOKEN``.
        service_id: Fastly service id; falls back to ``FASTLY_SERVICE_ID``.

    Raises:
        ConfigError: if either value is missing (or empty) after resolution.
    """
    overrides: dict[str, str] = {}
    if token is not None:
        overrides[TOKEN_ENV] = token
    if service_id is not None:
        overrides[SERVICE_ID_ENV] = service_id

    try:
        return Settings(**overrides)
    except ValidationError as exc:
        missing = [
            _ENV_NAMES.get(str(error["loc"][0]), str(error["loc"][0]))
            for error in exc.errors()
        ]
        raise ConfigError(
            "missing required configuration: " + ", ".join(missing)
        ) from exc
