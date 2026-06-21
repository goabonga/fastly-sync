# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Chris <goabonga@pm.me>

"""Command-line entry point for fastly-sync."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .blocklist import load_blocklist
from .config import load_settings
from .errors import FastlySyncError
from .fastly import FastlyClient
from .spec import build_desired_state, load_spec
from .sync import synchronize
from .waf import DEFAULT_ACL_NAME, bootstrap_acl, synchronize_blocklist


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="fastly-sync",
        description=(
            "Synchronise Fastly CDN and rate limiter configuration from a "
            "local or remote OpenAPI (openapi.json) document."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"fastly-sync {__version__}",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    sync_parser = subcommands.add_parser(
        "sync",
        help="synchronise CDN and rate limiter config from an OpenAPI spec",
    )
    sync_parser.add_argument(
        "--openapi",
        required=True,
        metavar="PATH_OR_URL",
        help="path or http(s) URL to the openapi.json document",
    )
    sync_parser.add_argument(
        "--token",
        help="Fastly API token (defaults to $FASTLY_API_TOKEN)",
    )
    sync_parser.add_argument(
        "--service-id",
        help="Fastly service id (defaults to $FASTLY_SERVICE_ID)",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the changes without applying them",
    )
    sync_parser.set_defaults(func=_cmd_sync)

    waf_parser = subcommands.add_parser(
        "waf",
        help="synchronise the WAF IP blocklist (Edge ACL) from a text file",
    )
    waf_parser.add_argument(
        "--blocklist",
        required=True,
        metavar="PATH_OR_URL",
        help="path or http(s) URL to the IP/CIDR blocklist (one per line)",
    )
    waf_parser.add_argument(
        "--acl-name",
        default=DEFAULT_ACL_NAME,
        help=f"name of the Edge ACL (default: {DEFAULT_ACL_NAME})",
    )
    waf_parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="create the ACL and enforcing VCL snippet before syncing entries",
    )
    waf_parser.add_argument(
        "--token",
        help="Fastly API token (defaults to $FASTLY_API_TOKEN)",
    )
    waf_parser.add_argument(
        "--service-id",
        help="Fastly service id (defaults to $FASTLY_SERVICE_ID)",
    )
    waf_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the changes without applying them",
    )
    waf_parser.set_defaults(func=_cmd_waf)
    return parser


def _cmd_sync(args: argparse.Namespace) -> int:
    settings = load_settings(args.token, args.service_id)
    spec = load_spec(args.openapi)
    state = build_desired_state(spec)
    with FastlyClient(settings.token, settings.service_id) as client:
        result = synchronize(state, client, dry_run=args.dry_run)

    verb = "would apply" if result.dry_run else "applied"
    print(f"fastly-sync: {verb} {len(result.applied)} change(s)")
    for action in result.applied:
        print(f"  [{action.kind}] {action.name} ({action.detail})")
    return 0


def _cmd_waf(args: argparse.Namespace) -> int:
    settings = load_settings(args.token, args.service_id)
    entries = load_blocklist(args.blocklist)
    with FastlyClient(settings.token, settings.service_id) as client:
        if args.bootstrap:
            bootstrap_acl(client, args.acl_name)
        result = synchronize_blocklist(
            entries, client, args.acl_name, dry_run=args.dry_run
        )

    verb = "would apply" if result.dry_run else "applied"
    print(
        f"fastly-sync: {verb} blocklist '{result.acl_name}': "
        f"+{len(result.added)} / -{len(result.removed)}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected sub-command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        exit_code: int = args.func(args)
    except FastlySyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
