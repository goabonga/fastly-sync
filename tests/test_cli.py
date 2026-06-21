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


def _creds(*extra):
    return ["--token", "t", "--service-id", "s", *extra]


def _sync_handler(request):
    path = request.url.path
    if path.endswith("/clone"):
        return httpx.Response(200, json={"number": 2})
    if path.endswith("/version") and request.method == "GET":
        return httpx.Response(200, json=[{"number": 1, "active": True}])
    if request.method == "GET":  # list_* for prune -> nothing to prune
        return httpx.Response(200, json=[])
    return httpx.Response(200, json={})


def _sync_prune_handler(request):
    path = request.url.path
    if path.endswith("/clone"):
        return httpx.Response(200, json={"number": 2})
    if path.endswith("/version") and request.method == "GET":
        return httpx.Response(200, json=[{"number": 1, "active": True}])
    if path.endswith("/condition") and request.method == "GET":
        return httpx.Response(200, json=[{"name": "cache-old"}])
    if request.method == "GET":
        return httpx.Response(200, json=[])
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


def _all_handler(request):
    # Serves both the OpenAPI sync (version/clone/list_*) and the WAF (acl) calls.
    path = request.url.path
    if path.endswith("/clone"):
        return httpx.Response(200, json={"number": 2})
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
    if request.method == "GET":  # list_* for prune -> nothing to prune
        return httpx.Response(200, json=[])
    return httpx.Response(200, json={})


def _show_handler(request):
    path = request.url.path
    if path.endswith("/version") and request.method == "GET":
        return httpx.Response(200, json=[{"number": 3, "active": True}])
    if path.endswith("/condition"):
        return httpx.Response(
            200, json=[{"name": "cache-widgets", "comment": "Public widgets"}]
        )
    if path.endswith("/cache_settings"):
        return httpx.Response(
            200,
            json=[
                {
                    "name": "/widgets",
                    "action": "cache",
                    "ttl": 60,
                    "cache_condition": "cache-widgets",
                }
            ],
        )
    if path.endswith("/rate-limiters"):
        return httpx.Response(
            200,
            json=[
                {
                    "id": "r1",
                    "name": "fsync-widgets",
                    "rps_limit": 100,
                    "window_size": 60,
                }
            ],
        )
    if path.endswith("/acl"):
        return httpx.Response(200, json=[{"name": "waf_blocklist", "id": "ACL1"}])
    if "/acl/ACL1/entries" in path:
        return httpx.Response(
            200,
            json=[{"id": "e1", "ip": "198.51.100.7", "subnet": None, "comment": "bad"}],
        )
    return httpx.Response(200, json=[])


# --- top level ---------------------------------------------------------


def test_version_flag():
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert "fastly-sync" in result.output


def test_no_args_shows_help():
    result = runner.invoke(cli.app, [])
    assert result.exit_code != 0
    assert "Usage" in result.output


# --- sync <target> -----------------------------------------------------


def _all_args(tmp_path, *extra):
    return [
        "sync",
        "all",
        "--openapi",
        _write_spec(tmp_path),
        "--blocklist",
        _write_blocklist(tmp_path),
        *_creds(*extra),
    ]


def test_sync_all_requires_blocklist(tmp_path):
    result = runner.invoke(
        cli.app, ["sync", "all", "--openapi", _write_spec(tmp_path), *_creds()]
    )
    assert result.exit_code != 0
    assert "blocklist" in result.output.lower()


def test_sync_all_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_all_handler))
    result = runner.invoke(cli.app, _all_args(tmp_path, "--dry-run"))
    assert result.exit_code == 0
    assert "fastly-sync plan: 2 to apply, 0 to prune" in result.output
    assert "fastly-sync plan: ACL 'waf_blocklist' +2 / -1" in result.output
    assert "(dry run" in result.output


def test_sync_all_applies_cdn_ratelimiter_and_waf(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_all_handler))
    result = runner.invoke(cli.app, _all_args(tmp_path, "--no-confirm"))
    assert result.exit_code == 0
    assert "applied 2 change(s)" in result.output
    assert "applied blocklist 'waf_blocklist': +2 / -1" in result.output


def test_sync_all_confirm_abort(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_all_handler))
    result = runner.invoke(cli.app, _all_args(tmp_path), input="n\n")
    assert result.exit_code == 0
    assert "aborted, nothing applied" in result.output
    assert "applied 2 change(s)" not in result.output


def test_sync_all_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_all_handler))
    result = runner.invoke(cli.app, _all_args(tmp_path, "--bootstrap", "--no-confirm"))
    assert result.exit_code == 0
    assert "plan: bootstrap ACL 'waf_blocklist'" in result.output
    assert "applied blocklist 'waf_blocklist': +2 / -1" in result.output


def test_sync_cdn_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_handler))
    result = runner.invoke(
        cli.app,
        ["sync", "cdn", "--openapi", _write_spec(tmp_path), *_creds("--dry-run")],
    )
    assert result.exit_code == 0
    assert "fastly-sync plan: 1 to apply" in result.output
    assert "[cdn] /widgets" in result.output
    assert "ratelimiter" not in result.output


def test_sync_cdn_confirm_abort(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_handler))
    result = runner.invoke(
        cli.app,
        ["sync", "cdn", "--openapi", _write_spec(tmp_path), *_creds()],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "aborted, nothing applied" in result.output


def test_sync_rate_limiter_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "rate-limiter",
            "--openapi",
            _write_spec(tmp_path),
            *_creds("--dry-run"),
        ],
    )
    assert result.exit_code == 0
    assert "[cdn]" not in result.output
    assert "[ratelimiter] widgets" in result.output


def test_sync_cdn_prunes_orphans(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_prune_handler))
    result = runner.invoke(
        cli.app,
        ["sync", "cdn", "--openapi", _write_spec(tmp_path), *_creds("--no-confirm")],
    )
    assert result.exit_code == 0
    assert "pruned 1 orphan(s)" in result.output
    assert "- [cdn-condition] cache-old" in result.output


def test_sync_cdn_no_prune(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_sync_prune_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "cdn",
            "--openapi",
            _write_spec(tmp_path),
            *_creds("--no-prune", "--no-confirm"),
        ],
    )
    assert result.exit_code == 0
    assert "pruned 0 orphan(s)" in result.output


def test_sync_reports_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise ConfigError("missing required configuration: FASTLY_API_TOKEN")

    monkeypatch.setattr(cli, "load_settings", boom)
    result = runner.invoke(cli.app, ["sync", "cdn", "--openapi", "ignored"])
    assert result.exit_code == 1
    assert "error: missing required configuration" in result.output


# --- sync waf ----------------------------------------------------------


def test_sync_waf_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "waf",
            "--blocklist",
            _write_blocklist(tmp_path),
            *_creds("--dry-run"),
        ],
    )
    assert result.exit_code == 0
    assert "fastly-sync plan: ACL 'waf_blocklist' +2 / -1" in result.output
    assert "(dry run" in result.output


def test_sync_waf_apply_no_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "waf",
            "--blocklist",
            _write_blocklist(tmp_path),
            *_creds("--no-confirm"),
        ],
    )
    assert result.exit_code == 0
    assert "applied blocklist 'waf_blocklist': +2 / -1" in result.output


def test_sync_waf_apply_abort(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app,
        ["sync", "waf", "--blocklist", _write_blocklist(tmp_path), *_creds()],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "aborted, nothing applied" in result.output


def test_sync_waf_bootstrap_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "waf",
            "--blocklist",
            _write_blocklist(tmp_path),
            *_creds("--bootstrap", "--dry-run"),
        ],
    )
    assert result.exit_code == 0
    assert "plan: bootstrap ACL 'waf_blocklist' and load 2 entry(ies)" in result.output
    assert "(dry run" in result.output


def test_sync_waf_bootstrap_abort(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "waf",
            "--blocklist",
            _write_blocklist(tmp_path),
            *_creds("--bootstrap"),
        ],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "aborted, nothing applied" in result.output


def test_sync_waf_bootstrap_and_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_waf_handler))
    result = runner.invoke(
        cli.app,
        [
            "sync",
            "waf",
            "--blocklist",
            _write_blocklist(tmp_path),
            *_creds("--bootstrap", "--no-confirm"),
        ],
    )
    assert result.exit_code == 0
    assert "plan: bootstrap ACL 'waf_blocklist'" in result.output
    assert "applied blocklist 'waf_blocklist': +2 / -1" in result.output


# --- show <target> -----------------------------------------------------


def test_show_all(monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_show_handler))
    result = runner.invoke(cli.app, ["show", "all", *_creds()])
    assert result.exit_code == 0
    assert "active version 3" in result.output
    assert "/widgets  action=cache ttl=60" in result.output
    assert "fsync-widgets  100 req / 60s" in result.output
    assert "WAF blocklist 'waf_blocklist' (1)" in result.output
    assert "198.51.100.7  # bad" in result.output


def test_show_cdn(monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_show_handler))
    result = runner.invoke(cli.app, ["show", "cdn", *_creds()])
    assert result.exit_code == 0
    assert "/widgets  action=cache ttl=60  # Public widgets" in result.output
    assert "WAF blocklist" not in result.output


def test_show_rate_limiter(monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_show_handler))
    result = runner.invoke(cli.app, ["show", "rate-limiter", *_creds()])
    assert result.exit_code == 0
    assert "fsync-widgets  100 req / 60s" in result.output
    assert "CDN cache settings" not in result.output


def test_show_cdn_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_show_handler))
    out = tmp_path / "cdn.txt"
    result = runner.invoke(cli.app, ["show", "cdn", *_creds("--output", str(out))])
    assert result.exit_code == 0
    written = out.read_text(encoding="utf-8")
    assert "active version 3" in written
    assert "/widgets  action=cache ttl=60" in written
    assert f"wrote to {out}" in result.output


def test_show_rate_limiter_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_show_handler))
    out = tmp_path / "rl.txt"
    result = runner.invoke(
        cli.app, ["show", "rate-limiter", *_creds("--output", str(out))]
    )
    assert result.exit_code == 0
    assert "fsync-widgets  100 req / 60s" in out.read_text(encoding="utf-8")


def test_show_waf_stdout(monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_show_handler))
    result = runner.invoke(cli.app, ["show", "waf", *_creds()])
    assert result.exit_code == 0
    assert "WAF blocklist 'waf_blocklist' (1)" in result.output
    assert "198.51.100.7  # bad" in result.output


def test_show_waf_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FastlyClient", _factory(_show_handler))
    out = tmp_path / "exported.txt"
    result = runner.invoke(cli.app, ["show", "waf", *_creds("--output", str(out))])
    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8") == "198.51.100.7  # bad\n"
    assert "wrote 1 entry(ies)" in result.output
