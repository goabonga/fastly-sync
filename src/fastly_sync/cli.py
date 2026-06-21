# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Command-line entry point for fastly-sync (built with Typer)."""

from __future__ import annotations

from collections.abc import Callable, Collection
from pathlib import Path

import typer

from . import __version__
from .blocklist import dump_blocklist, load_blocklist
from .config import load_settings
from .errors import FastlySyncError
from .fastly import FastlyClient
from .models import SyncResult
from .show import LiveConfig, gather
from .spec import build_desired_state, load_spec
from .sync import ALL_COMPONENTS, Component, select_state, synchronize
from .waf import DEFAULT_ACL_NAME, bootstrap_acl, synchronize_blocklist

app = typer.Typer(
    no_args_is_help=True,
    add_completion=True,
    help=(
        "Synchronise Fastly configuration on demand: CDN cache and rate "
        "limiters from a local or remote OpenAPI (openapi.json) document, "
        "and the WAF IP blocklist (Edge ACL) from a text file."
    ),
)
sync_app = typer.Typer(
    no_args_is_help=True, help="Apply configuration to Fastly (per target)."
)
show_app = typer.Typer(
    no_args_is_help=True, help="Show the live configuration applied on Fastly."
)
app.add_typer(sync_app, name="sync")
app.add_typer(show_app, name="show")

_OPENAPI_OPTION = typer.Option(
    ..., "--openapi", metavar="PATH_OR_URL", help="path or URL to openapi.json"
)
_PRUNE_OPTION = typer.Option(
    True,
    "--prune/--no-prune",
    help="delete managed objects no longer in the spec (default: on)",
)
_ACL_NAME_OPTION = typer.Option(
    DEFAULT_ACL_NAME, "--acl-name", help="name of the WAF Edge ACL"
)
_TOKEN_OPTION = typer.Option(
    None, "--token", help="Fastly API token ($FASTLY_API_TOKEN)"
)
_SERVICE_OPTION = typer.Option(
    None, "--service-id", help="Fastly service id ($FASTLY_SERVICE_ID)"
)
_DRY_RUN_OPTION = typer.Option(
    False, "--dry-run", help="report the plan without applying it"
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


# --- sync <target> -----------------------------------------------------


def _run_openapi_sync(
    *,
    openapi: str,
    components: Collection[Component],
    prune: bool,
    no_confirm: bool,
    dry_run: bool,
    token: str | None,
    service_id: str | None,
) -> None:
    def run() -> None:
        settings = load_settings(token, service_id)
        state = select_state(build_desired_state(load_spec(openapi)), components)
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


@sync_app.command("cdn")
def sync_cdn(
    openapi: str = _OPENAPI_OPTION,
    prune: bool = _PRUNE_OPTION,
    no_confirm: bool = _NO_CONFIRM_OPTION,
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
    dry_run: bool = _DRY_RUN_OPTION,
) -> None:
    """Synchronise only the CDN cache settings."""
    _run_openapi_sync(
        openapi=openapi,
        components=frozenset({Component.CDN}),
        prune=prune,
        no_confirm=no_confirm,
        dry_run=dry_run,
        token=token,
        service_id=service_id,
    )


@sync_app.command("rate-limiter")
def sync_rate_limiter(
    openapi: str = _OPENAPI_OPTION,
    prune: bool = _PRUNE_OPTION,
    no_confirm: bool = _NO_CONFIRM_OPTION,
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
    dry_run: bool = _DRY_RUN_OPTION,
) -> None:
    """Synchronise only the rate limiters."""
    _run_openapi_sync(
        openapi=openapi,
        components=frozenset({Component.RATELIMIT}),
        prune=prune,
        no_confirm=no_confirm,
        dry_run=dry_run,
        token=token,
        service_id=service_id,
    )


@sync_app.command("all")
def sync_all(
    openapi: str = _OPENAPI_OPTION,
    blocklist: str = typer.Option(
        ...,
        "--blocklist",
        metavar="PATH_OR_URL",
        help="path or URL to the IP/CIDR blocklist (one per line)",
    ),
    acl_name: str = _ACL_NAME_OPTION,
    bootstrap: bool = typer.Option(
        False, "--bootstrap", help="create the WAF ACL and VCL snippet before syncing"
    ),
    prune: bool = _PRUNE_OPTION,
    no_confirm: bool = _NO_CONFIRM_OPTION,
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
    dry_run: bool = _DRY_RUN_OPTION,
) -> None:
    """Synchronise CDN cache, rate limiters and the WAF blocklist together."""

    def run() -> None:
        settings = load_settings(token, service_id)
        state = select_state(build_desired_state(load_spec(openapi)), ALL_COMPONENTS)
        entries = load_blocklist(blocklist)
        with FastlyClient(settings.token, settings.service_id) as client:
            plan = synchronize(
                state, client, components=ALL_COMPONENTS, prune=prune, dry_run=True
            )
            _echo_sync_plan(plan)
            if bootstrap:
                typer.echo(
                    f"fastly-sync plan: bootstrap ACL '{acl_name}' and load "
                    f"{len(entries)} entry(ies)"
                )
            else:
                waf_plan = synchronize_blocklist(
                    entries, client, acl_name, dry_run=True
                )
                typer.echo(
                    f"fastly-sync plan: ACL '{acl_name}' "
                    f"+{len(waf_plan.added)} / -{len(waf_plan.removed)}"
                )
            if dry_run:
                typer.echo("(dry run — nothing applied)")
                return
            if not _confirmed(no_confirm):
                return
            result = synchronize(
                state, client, components=ALL_COMPONENTS, prune=prune, dry_run=False
            )
            typer.echo(
                f"fastly-sync: applied {len(result.applied)} change(s), "
                f"pruned {len(result.removed)} orphan(s)"
            )
            if bootstrap:
                bootstrap_acl(client, acl_name)
            waf_result = synchronize_blocklist(entries, client, acl_name, dry_run=False)
            typer.echo(
                f"fastly-sync: applied blocklist '{waf_result.acl_name}': "
                f"+{len(waf_result.added)} / -{len(waf_result.removed)}"
            )

    _guard(run)


@sync_app.command("waf")
def sync_waf(
    blocklist: str = typer.Option(
        ...,
        "--blocklist",
        metavar="PATH_OR_URL",
        help="path or URL to the IP/CIDR blocklist (one per line)",
    ),
    acl_name: str = _ACL_NAME_OPTION,
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


# --- show <target> -----------------------------------------------------

_OUTPUT_OPTION = typer.Option(
    None, "--output", "-o", help="write the output to this file instead of stdout"
)


def _lines(*parts: str) -> str:
    return "".join(f"{part}\n" for part in parts)


def _render_version(config: LiveConfig) -> str:
    return _lines(f"fastly-sync: active version {config.version}")


def _render_cdn(config: LiveConfig) -> str:
    lines = [f"CDN cache settings ({len(config.cache_settings)}):"]
    lines += [
        f"  {setting.get('name')}  "
        f"action={setting.get('action')} ttl={setting.get('ttl')}"
        for setting in config.cache_settings
    ]
    return _lines(*lines)


def _render_rate_limiters(config: LiveConfig) -> str:
    lines = [f"Rate limiters ({len(config.rate_limiters)}):"]
    lines += [
        f"  {limiter.get('name')}  "
        f"{limiter.get('rps_limit')} req / {limiter.get('window_size')}s"
        for limiter in config.rate_limiters
    ]
    return _lines(*lines)


def _render_waf(config: LiveConfig, acl_name: str) -> str:
    lines = [f"WAF blocklist '{acl_name}' ({len(config.blocklist)}):"]
    for entry in config.blocklist:
        cidr = entry.ip if entry.subnet is None else f"{entry.ip}/{entry.subnet}"
        suffix = f"  # {entry.comment}" if entry.comment else ""
        lines.append(f"  {cidr}{suffix}")
    return _lines(*lines)


def _emit(text: str, output: Path | None) -> None:
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"fastly-sync: wrote to {output}", err=True)


def _show(
    token: str | None,
    service_id: str | None,
    render: Callable[[LiveConfig], str],
    output: Path | None,
    acl_name: str = DEFAULT_ACL_NAME,
) -> None:
    def run() -> None:
        settings = load_settings(token, service_id)
        with FastlyClient(settings.token, settings.service_id) as client:
            config = gather(client, acl_name)
        _emit(render(config), output)

    _guard(run)


@show_app.command("cdn")
def show_cdn(
    output: Path | None = _OUTPUT_OPTION,
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
) -> None:
    """Show the live CDN cache settings (``--output`` writes to a file)."""
    _show(
        token,
        service_id,
        lambda config: _render_version(config) + _render_cdn(config),
        output,
    )


@show_app.command("rate-limiter")
def show_rate_limiter(
    output: Path | None = _OUTPUT_OPTION,
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
) -> None:
    """Show the live rate limiters (``--output`` writes to a file)."""
    _show(
        token,
        service_id,
        lambda config: _render_version(config) + _render_rate_limiters(config),
        output,
    )


@show_app.command("all")
def show_all(
    acl_name: str = _ACL_NAME_OPTION,
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
) -> None:
    """Show the live CDN, rate limiter and WAF config."""

    def render(config: LiveConfig) -> str:
        return (
            _render_version(config)
            + _render_cdn(config)
            + _render_rate_limiters(config)
            + _render_waf(config, acl_name)
        )

    _show(token, service_id, render, None, acl_name)


@show_app.command("waf")
def show_waf(
    acl_name: str = _ACL_NAME_OPTION,
    output: Path | None = _OUTPUT_OPTION,
    token: str | None = _TOKEN_OPTION,
    service_id: str | None = _SERVICE_OPTION,
) -> None:
    """Show the live WAF blocklist (``--output`` writes the blocklist format)."""

    def run() -> None:
        settings = load_settings(token, service_id)
        with FastlyClient(settings.token, settings.service_id) as client:
            config = gather(client, acl_name)
        if output is None:
            typer.echo(
                _render_version(config) + _render_waf(config, acl_name), nl=False
            )
        else:
            output.write_text(dump_blocklist(config.blocklist), encoding="utf-8")
            typer.echo(
                f"fastly-sync: wrote {len(config.blocklist)} entry(ies) to {output}",
                err=True,
            )

    _guard(run)
