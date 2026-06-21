# fastly-sync

[![CI](https://github.com/goabonga/fastly-sync/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/goabonga/fastly-sync/actions/workflows/ci.yml)
[![Codecov](https://img.shields.io/codecov/c/github/goabonga/fastly-sync?logo=codecov)](https://codecov.io/gh/goabonga/fastly-sync)
[![PyPI](https://img.shields.io/pypi/v/fastly-sync.svg)](https://pypi.org/project/fastly-sync/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/goabonga/fastly-sync/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

`fastly-sync` synchronises [Fastly](https://www.fastly.com/) configuration on
demand from declarative sources:

- **CDN cache** and **rate limiters** are derived from an OpenAPI
  (`openapi.json`) document (local file or remote `http(s)` URL). Each path
  becomes a CDN cache setting whose policy is derived from its methods
  (GET/HEAD-only paths are cached, paths with mutating methods pass) and can be
  tuned with an `x-fastly-cache` extension; paths carrying an
  `x-fastly-ratelimit` extension become Fastly rate limiters.
- **WAF IP blacklisting** is driven by a text blocklist (local or remote),
  reconciled onto a Fastly Edge ACL.

## Documentation

The project site is published from `main` to GitHub Pages:
<https://goabonga.github.io/fastly-sync/>.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for environment management
- A Fastly API token and the target service id

## Install

```bash
pip install fastly-sync      # or: uvx fastly-sync --help
```

## Usage

```bash
export FASTLY_API_TOKEN=...        # or pass --token
export FASTLY_SERVICE_ID=...       # or pass --service-id

# Apply EVERYTHING: CDN cache + rate limiters (one clone/activate) AND the WAF
# blocklist. Prints a plan, asks for confirmation, then applies.
fastly-sync sync all --openapi ./openapi.json --blocklist ./blocklist.txt
fastly-sync sync all --openapi ./openapi.json --blocklist ./blocklist.txt --dry-run
fastly-sync sync all --openapi ./openapi.json --blocklist ./blocklist.txt --no-confirm

# Apply a single target:
fastly-sync sync cdn          --openapi ./openapi.json
fastly-sync sync rate-limiter --openapi ./openapi.json
fastly-sync sync waf          --blocklist ./blocklist.txt

# WAF IP blacklisting: reconcile an Edge ACL from a text blocklist.
fastly-sync sync waf --blocklist ./blocklist.txt --bootstrap   # one-time ACL + VCL
fastly-sync sync waf --blocklist https://feeds.example.com/bad-ips.txt
fastly-sync sync waf --blocklist ./blocklist.txt --dry-run

# Show the live config applied on Fastly, per target (or `all`).
# show cdn / rate-limiter / waf accept --output FILE to write instead of printing.
fastly-sync show all
fastly-sync show cdn
fastly-sync show cdn --output cdn.txt          # same text, to a file
fastly-sync show rate-limiter
fastly-sync show waf                           # print the blocklist
fastly-sync show waf --output blocklist.txt    # export the blocklist (round-trips)

# Shell completion (Typer):
fastly-sync --install-completion
```

### WAF IP blacklisting

`fastly-sync sync waf` reconciles a Fastly **Edge ACL** from a text blocklist
(one IP or CIDR per line, `#` for comments), local or remote. Entries are
validated with Python's `ipaddress`, so IPv4, IPv6 and subnets all work:

```
# blocklist.txt
203.0.113.0/24      # botnet C2
198.51.100.7
2001:db8::/32
```

The reconciliation is a diff: missing IPs are **added** and stale ones
**removed** (`--dry-run` reports without applying). Because ACL *entries* are
mutable outside service versioning, ongoing syncs are fast and need no version
bump.

Run once with `--bootstrap` to create the ACL and an enforcing `vcl_recv`
snippet (`if (client.ip ~ waf_blocklist) { error 403 "Forbidden"; }`) on a new
activated version; subsequent runs only touch the ACL entries. Use
`--acl-name` to target a differently named ACL (default `waf_blocklist`).

`fastly-sync show waf` prints the live ACL entries; with `--output FILE` it
writes them in the blocklist text format, so you can snapshot, diff or version
the current blocklist. The output round-trips back through `sync waf`.

### OpenAPI CDN cache extension

By default the cache policy is derived from a path's methods: a GET/HEAD-only
path is **cached** (TTL 3600s), any path exposing a mutating method (POST, PUT,
…) is set to **pass**. Override per path with the `x-fastly-cache` extension:

```json
{
  "paths": {
    "/widgets": {
      "get": {},
      "x-fastly-cache": {
        "action": "cache",
        "ttl": 3600,
        "stale_while_revalidate": 60,
        "stale_if_error": 300,
        "description": "Public widgets API"
      }
    }
  }
}
```

`action` is `cache` or `pass` (defaults to the method-derived value); `ttl`
defaults to 3600s when caching; `stale_if_error` maps to Fastly's serve-stale
window (`stale_ttl`). `description` is an optional note — the Fastly
`cache_settings` object has no comment field, so it is stored on the endpoint's
**condition** `comment` and shown by `fastly-sync show cdn`. (Rate limiters have
no comment field in the Fastly API, so they cannot carry a description; ACL
entries do — use the blocklist's `# comment`.)

Each cache setting is scoped to its path by a Fastly **request condition**
built as a strict, anchored regex: each `{param}` becomes a single non-slash
segment and both ends are anchored, e.g. `/widgets/{id}` →
`req.url ~ "^/widgets/[^/]+(?:\?|$)"`. Sibling paths such as `/widget` and
`/widgets` therefore never overlap. When `stale_while_revalidate` (or
`stale_if_error`) is set on a cached path, a `Surrogate-Control` response header
carrying those directives is emitted and scoped to the same condition.

### OpenAPI rate limiter extension

A path opts into a rate limiter with the `x-fastly-ratelimit` extension:

```json
{
  "paths": {
    "/widgets": {
      "get": {},
      "x-fastly-ratelimit": { "name": "widgets", "limit": 100, "window": 60 }
    }
  }
}
```

`limit` is required (requests per `window`); `window` defaults to 60 seconds
and `name` defaults to a slug of the path.

### Reconciliation, pruning and confirmation

`sync` is **declarative**: the OpenAPI document is the source of truth. On each
run it not only creates/updates the managed objects but also **prunes** the
ones it previously created that are no longer in the spec (a removed path's
cache setting / condition / serve-stale header, a removed rate limiter). Pass
`--no-prune` to keep additive behaviour.

Pruning is **scoped to objects fastly-sync owns** — managed rate limiters are
named with an `fsync-` prefix and cache settings are matched through their
`cache-…` condition — so configuration created by hand is never deleted. A
targeted run (`sync cdn` / `sync rate-limiter`) only ever touches that target.

Before applying, every `sync` target prints a **plan** and asks for
confirmation. Use `--dry-run` to print the plan and stop, or `--no-confirm` to
apply without prompting (CI). `show <target>` prints the live CDN, rate limiter
and WAF configuration currently applied on Fastly.

## Getting started (development)

```bash
git clone https://github.com/goabonga/fastly-sync.git
cd fastly-sync
uv sync
uv run pre-commit install
uv run ruff check src tests
uv run pytest
```

## Versioning and release

Versions are bumped from
[Conventional Commits](https://www.conventionalcommits.org/) by
[multicz](https://github.com/goabonga/multicz). On every push to `main`, CI
computes the bump, writes the changelog, tags, and publishes to PyPI.
Maintainers do not bump versions or edit the changelog by hand.

## Stability and deprecation policy

`fastly-sync` follows [Semantic Versioning](https://semver.org/) and the
standard Python `n + 2` deprecation cadence (announce + warn in one release,
remove in the release after the next). Full policy:
[`docs/stability.md`](docs/stability.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, the commit-message
convention, and the test/lint expectations. By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

Security issues: please follow the disclosure process in
[SECURITY.md](SECURITY.md).

## License

Distributed under the [MIT License](LICENSE).
