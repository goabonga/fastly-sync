# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import json

import httpx
import pytest

from fastly_sync import cli
from fastly_sync.errors import ConfigError
from fastly_sync.fastly import BASE_URL, FastlyClient

SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/widgets": {
            "get": {},
            "x-fastly-ratelimit": {"limit": 100, "name": "widgets"},
        }
    },
}


def _write_spec(tmp_path):
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(SPEC), encoding="utf-8")
    return str(path)


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "fastly-sync" in capsys.readouterr().out


def test_missing_subcommand_errors():
    with pytest.raises(SystemExit):
        cli.main([])


def test_sync_dry_run(tmp_path, capsys, monkeypatch):
    spec_path = _write_spec(tmp_path)

    def fake_factory(token, service_id, **kwargs):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=[{"number": 1, "active": True}])
        )
        inner = httpx.Client(transport=transport, base_url=BASE_URL)
        return FastlyClient(token, service_id, client=inner)

    monkeypatch.setattr(cli, "FastlyClient", fake_factory)
    exit_code = cli.main(
        [
            "sync",
            "--openapi",
            spec_path,
            "--token",
            "t",
            "--service-id",
            "s",
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "would apply 2 change(s)" in out
    assert "[cdn] /widgets" in out
    assert "[ratelimiter] widgets" in out


def test_sync_apply(tmp_path, capsys, monkeypatch):
    spec_path = _write_spec(tmp_path)

    def handler(request):
        if request.url.path.endswith("/clone"):
            return httpx.Response(200, json={"number": 2})
        if request.method == "GET":
            return httpx.Response(200, json=[{"number": 1, "active": True}])
        return httpx.Response(200, json={})

    def fake_factory(token, service_id, **kwargs):
        inner = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)
        return FastlyClient(token, service_id, client=inner)

    monkeypatch.setattr(cli, "FastlyClient", fake_factory)
    exit_code = cli.main(
        ["sync", "--openapi", spec_path, "--token", "t", "--service-id", "s"]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "applied 2 change(s)" in out


def test_sync_reports_errors(tmp_path, capsys, monkeypatch):
    def boom(*args, **kwargs):
        raise ConfigError("missing required configuration: FASTLY_API_TOKEN")

    monkeypatch.setattr(cli, "load_settings", boom)
    exit_code = cli.main(["sync", "--openapi", "ignored"])
    err = capsys.readouterr().err
    assert exit_code == 1
    assert "error: missing required configuration" in err
