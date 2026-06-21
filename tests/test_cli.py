# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

import json

import httpx
from typer.testing import CliRunner

from fastly_sync import cli
from fastly_sync.errors import ConfigError
from fastly_sync.fastly import BASE_URL, FastlyClient

runner = CliRunner()

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


def _write_blocklist(tmp_path):
    path = tmp_path / "blocklist.txt"
    path.write_text("198.51.100.7\n203.0.113.0/24\n", encoding="utf-8")
    return str(path)


def _factory(handler):
    def make(token, service_id, **kwargs):
        inner = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE_URL)
        return FastlyClient(token, service_id, client=inner)

    return make


def _sync_handler(request):
    if request.url.path.endswith("/clone"):
        return httpx.Response(200, json={"number": 2})
    if request.method == "GET":
        return httpx.Response(200, json=[{"number": 1, "active": True}])
    return httpx.Response(200, json={})


def _waf_handler(request):
    path = request.url.path
    if path.endswith("/version") and request.method == "GET":
        return httpx.Response(200, json=[{"number": 1, "active": True}])
    if path.endswith("/acl") and request.method == "POST":
        return httpx.Response(200, json={"id": "ACL2"})
    if path.endswith("/acl") and request.method == "GET":
        return httpx.Response(200, json=[{"name": "waf_blocklist", "id": "ACL1"}])
    if "/acl/ACL1/entries" in path and request.method == "GET":
        return httpx.Response(
            200, json=[{"id": "e1", "ip": "192.0.2.1", "subnet": None, "comment": "x"}]
        )
    if path.endswith("/clone"):
        return httpx.Response(200, json={"number": 2})
    return httpx.Response(200, json={})


def test_version_flag():
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert "fastly-sync" in result.output


def test_no_args_shows_help():
    result = runner.invoke(cli.app, [])
    assert result.exit_code != 0
    assert "Usage" in result.output


def test_sync_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "--openapi",
            _write_spec(tmp_path),
            "--token",
            "t",
            "--service-id",
            "s",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "would apply 2 change(s)" in result.output
    assert "[cdn] /widgets" in result.output
    assert "[ratelimiter] widgets" in result.output


def test_sync_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "--openapi",
            _write_spec(tmp_path),
            "--token",
            "t",
            "--service-id",
            "s",
        ],
    )
    assert result.exit_code == 0
    assert "applied 2 change(s)" in result.output


def test_sync_only_cdn(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "--openapi",
            _write_spec(tmp_path),
            "--token",
            "t",
            "--service-id",
            "s",
            "--dry-run",
            "--only",
            "cdn",
        ],
    )
    assert result.exit_code == 0
    assert "would apply 1 change(s)" in result.output
    assert "[cdn] /widgets" in result.output
    assert "ratelimiter" not in result.output


def test_sync_skip_cdn(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "--openapi",
            _write_spec(tmp_path),
            "--token",
            "t",
            "--service-id",
            "s",
            "--dry-run",
            "--skip",
            "cdn",
        ],
    )
    assert result.exit_code == 0
    assert "[cdn]" not in result.output
    assert "[ratelimiter] widgets" in result.output


def test_sync_only_and_skip_conflict(tmp_path):
    result = runner.invoke(
        cli.app,
        ["sync", "--openapi", _write_spec(tmp_path), "--only", "cdn", "--skip", "cdn"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_sync_reports_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise ConfigError("missing required configuration: FASTLY_API_TOKEN")

    monkeypatch.setattr(cli, "load_settings", boom)
    result = runner.invoke(cli.app, ["sync", "--openapi", "ignored"])
    assert result.exit_code == 1
    assert "error: missing required configuration" in result.output


def test_waf_sync_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app,
        [
            "waf",
            "sync",
            "--blocklist",
            _write_blocklist(tmp_path),
            "--token",
            "t",
            "--service-id",
            "s",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    # Two desired IPs added, the existing 192.0.2.1 not in the file -> removed.
    assert "would apply blocklist 'waf_blocklist': +2 / -1" in result.output


def test_waf_sync_bootstrap_and_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app,
        [
            "waf",
            "sync",
            "--blocklist",
            _write_blocklist(tmp_path),
            "--token",
            "t",
            "--service-id",
            "s",
            "--bootstrap",
        ],
    )
    assert result.exit_code == 0
    assert "applied blocklist 'waf_blocklist': +2 / -1" in result.output


def test_waf_export_to_stdout(monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app, ["waf", "export", "--token", "t", "--service-id", "s"]
    )
    assert result.exit_code == 0
    assert "192.0.2.1  # x" in result.output


def test_waf_export_to_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    out = tmp_path / "exported.txt"
    result = runner.invoke(
        cli.app,
        ["waf", "export", "--token", "t", "--service-id", "s", "--output", str(out)],
    )
    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8") == "192.0.2.1  # x\n"
    assert "wrote 1 entry(ies)" in result.output
