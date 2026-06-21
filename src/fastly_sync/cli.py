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
from .models import SyncResult
from .show import gather
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
_NO_CONFIRM_OPTION = typer.Option(
    False, "--no-confirm", help="apply without the interactive confirmation prompt"
)


def _guard(action: Callable[[], None]) -> None:
    """Run a command body, mapping domain errors to a clean exit code 1."""
    try:
        action()
    except FastlySyncError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _confirmed(no_confirm: bool) -> bool:
    """Return whether to proceed: ``--no-confirm`` or a positive prompt answer."""
    if no_confirm or typer.confirm("Apply these changes?"):
        return True
    typer.echo("aborted, nothing applied")
    return False


def _echo_sync_plan(plan: SyncResult) -> None:
    typer.echo(
        f"fastly-sync plan: {len(plan.applied)} to apply, {len(plan.removed)} to prune"
    )
    for action in plan.applied:
        typer.echo(f"  ~ [{action.kind}] {action.name} ({action.detail})")
    for action in plan.removed:
        typer.echo(f"  - [{action.kind}] {action.name} ({action.detail})")


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
    no_confirm: bool = _NO_CONFIRM_OPTION,
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
            plan = synchronize(
                state, client, components=components, prune=prune, dry_run=True
            )
            _echo_sync_plan(plan)
            if dry_run:
                typer.echo("(dry run — nothing applied)")
                return
            if not _confirmed(no_confirm):
                return
            result = synchronize(
                state, client, components=components, prune=prune, dry_run=False
            )
            typer.echo(
                f"fastly-sync: applied {len(result.applied)} change(s), "
                f"pruned {len(result.removed)} orphan(s)"
            )

    _guard(run)


@app.command()
def show(
    acl_name: str = typer.Option(
        DEFAULT_ACL_NAME, "--acl-name", help="name of the WAF Edge ACL"
    ),
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
) -> None:
    """Show the live CDN, rate limiter and WAF config applied on Fastly."""

    def run() -> None:
        settings = load_settings(token, service_id)
        with FastlyClient(settings.token, settings.service_id) as client:
            config = gather(client, acl_name)

        typer.echo(f"fastly-sync: active version {config.version}")
        typer.echo(f"CDN cache settings ({len(config.cache_settings)}):")
        for setting in config.cache_settings:
            typer.echo(
                f"  {setting.get('name')}  "
                f"action={setting.get('action')} ttl={setting.get('ttl')}"
            )
        typer.echo(f"Rate limiters ({len(config.rate_limiters)}):")
        for limiter in config.rate_limiters:
            typer.echo(
                f"  {limiter.get('name')}  "
                f"{limiter.get('rps_limit')} req / {limiter.get('window_size')}s"
            )
        typer.echo(f"WAF blocklist '{acl_name}' ({len(config.blocklist)}):")
        for entry in config.blocklist:
            cidr = entry.ip if entry.subnet is None else f"{entry.ip}/{entry.subnet}"
            suffix = f"  # {entry.comment}" if entry.comment else ""
            typer.echo(f"  {cidr}{suffix}")

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
    no_confirm: bool = _NO_CONFIRM_OPTION,
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
                # The ACL does not exist yet, so a diff is not possible; the
                # plan is simply "create the ACL and load every entry".
                typer.echo(
                    f"fastly-sync plan: bootstrap ACL '{acl_name}' and load "
                    f"{len(entries)} entry(ies)"
                )
                if dry_run:
                    typer.echo("(dry run — nothing applied)")
                    return
                if not _confirmed(no_confirm):
                    return
                bootstrap_acl(client, acl_name)
            else:
                plan = synchronize_blocklist(entries, client, acl_name, dry_run=True)
                typer.echo(
                    f"fastly-sync plan: ACL '{acl_name}' "
                    f"+{len(plan.added)} / -{len(plan.removed)}"
                )
                if dry_run:
                    typer.echo("(dry run — nothing applied)")
                    return
                if not _confirmed(no_confirm):
                    return
            result = synchronize_blocklist(entries, client, acl_name, dry_run=False)
            typer.echo(
                f"fastly-sync: applied blocklist '{result.acl_name}': "
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
