# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Command-line entry point for fastly-sync (built with Typer)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from . import __version__
from .blocklist import dump_blocklist, load_blocklist
from .config import load_settings
from .errors import FastlySyncError
from .fastly import FastlyClient
from .spec import build_desired_state, load_spec
from .sync import Component, resolve_components, select_state, synchronize
from .waf import (
    DEFAULT_ACL_NAME,
    bootstrap_acl,
    export_blocklist,
    synchronize_blocklist,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=True,
    help=(
        "Synchronise Fastly configuration on demand: CDN cache and rate "
        "limiters from a local or remote OpenAPI (openapi.json) document, "
        "and the WAF IP blocklist (Edge ACL) from a text file."
    ),
)
waf_app = typer.Typer(
    no_args_is_help=True,
    help="Manage the WAF IP blocklist backed by a Fastly Edge ACL.",
)
app.add_typer(waf_app, name="waf")

_TOKEN_OPTION = typer.Option(
    None, "--token", help="Fastly API token ($FASTLY_API_TOKEN)"
)
_SERVICE_OPTION = typer.Option(
    None, "--service-id", help="Fastly service id ($FASTLY_SERVICE_ID)"
)
_DRY_RUN_OPTION = typer.Option(
    False, "--dry-run", help="report the changes without applying them"
)


def _guard(action: Callable[[], None]) -> None:
    """Run a command body, mapping domain errors to a clean exit code 1."""
    try:
        action()
    except FastlySyncError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"fastly-sync {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="show the version and exit",
    ),
) -> None:
    """fastly-sync command-line interface."""


@app.command()
def sync(
    openapi: str = typer.Option(
        ..., "--openapi", metavar="PATH_OR_URL", help="path or URL to openapi.json"
    ),
    only: Component | None = typer.Option(
        None, "--only", help="apply only this component (cdn or ratelimit)"
    ),
    skip: Component | None = typer.Option(
        None, "--skip", help="apply everything except this component"
    ),
    prune: bool = typer.Option(
        True,
        "--prune/--no-prune",
        help="delete managed objects no longer in the spec (default: on)",
    ),
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
    dry_run: bool = _DRY_RUN_OPTION,
) -> None:
    """Synchronise CDN cache and rate limiters from an OpenAPI document."""
    if only is not None and skip is not None:
        raise typer.BadParameter("--only and --skip are mutually exclusive")

    def run() -> None:
        settings = load_settings(token, service_id)
        components = resolve_components(only, skip)
        state = select_state(
            build_desired_state(load_spec(openapi)), only=only, skip=skip
        )
        with FastlyClient(settings.token, settings.service_id) as client:
            result = synchronize(
                state, client, components=components, prune=prune, dry_run=dry_run
            )
        verb = "would apply" if result.dry_run else "applied"
        pruned = "would prune" if result.dry_run else "pruned"
        typer.echo(
            f"fastly-sync: {verb} {len(result.applied)} change(s), "
            f"{pruned} {len(result.removed)} orphan(s)"
        )
        for action in result.applied:
            typer.echo(f"  [{action.kind}] {action.name} ({action.detail})")
        for action in result.removed:
            typer.echo(f"  - [{action.kind}] {action.name} ({action.detail})")

    _guard(run)


@waf_app.command("sync")
def waf_sync(
    blocklist: str = typer.Option(
        ...,
        "--blocklist",
        metavar="PATH_OR_URL",
        help="path or URL to the IP/CIDR blocklist (one per line)",
    ),
    acl_name: str = typer.Option(
        DEFAULT_ACL_NAME, "--acl-name", help="name of the Edge ACL"
    ),
    bootstrap: bool = typer.Option(
        False, "--bootstrap", help="create the ACL and VCL snippet before syncing"
    ),
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
    dry_run: bool = _DRY_RUN_OPTION,
) -> None:
    """Reconcile the WAF IP blocklist (Edge ACL) from a text file."""

    def run() -> None:
        settings = load_settings(token, service_id)
        entries = load_blocklist(blocklist)
        with FastlyClient(settings.token, settings.service_id) as client:
            if bootstrap:
                bootstrap_acl(client, acl_name)
            result = synchronize_blocklist(entries, client, acl_name, dry_run=dry_run)
        verb = "would apply" if result.dry_run else "applied"
        typer.echo(
            f"fastly-sync: {verb} blocklist '{result.acl_name}': "
            f"+{len(result.added)} / -{len(result.removed)}"
        )

    _guard(run)


@waf_app.command("export")
def waf_export(
    acl_name: str = typer.Option(
        DEFAULT_ACL_NAME, "--acl-name", help="name of the Edge ACL"
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="write to this file instead of stdout"
    ),
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
) -> None:
    """Export the current Edge ACL entries to the text blocklist format."""

    def run() -> None:
        settings = load_settings(token, service_id)
        with FastlyClient(settings.token, settings.service_id) as client:
            entries = export_blocklist(client, acl_name)
        text = dump_blocklist(entries)
        if output is None:
            typer.echo(text, nl=False)
        else:
            output.write_text(text, encoding="utf-8")
            typer.echo(
                f"fastly-sync: wrote {len(entries)} entry(ies) to {output}", err=True
            )

    _guard(run)
