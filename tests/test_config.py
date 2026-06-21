# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import pytest

from fastly_sync.config import SERVICE_ID_ENV, TOKEN_ENV, load_settings
from fastly_sync.errors import ConfigError


def test_explicit_values_win(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.delenv(SERVICE_ID_ENV, raising=False)
    settings = load_settings("tok", "svc")
    assert settings.token == "tok"
    assert settings.service_id == "svc"


def test_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "env-token")
    monkeypatch.setenv(SERVICE_ID_ENV, "env-service")
    settings = load_settings()
    assert settings.token == "env-token"
    assert settings.service_id == "env-service"


def test_empty_values_are_treated_as_missing(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "")
    monkeypatch.delenv(SERVICE_ID_ENV, raising=False)
    with pytest.raises(ConfigError) as excinfo:
        load_settings(service_id="svc")
    assert TOKEN_ENV in str(excinfo.value)


def test_reports_every_missing_value(monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.delenv(SERVICE_ID_ENV, raising=False)
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert TOKEN_ENV in message
    assert SERVICE_ID_ENV in message
