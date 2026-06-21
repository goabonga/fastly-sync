# fastly-sync

[![CI](https://github.com/goabonga/fastly-sync/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/goabonga/fastly-sync/actions/workflows/ci.yml)
[![Codecov](https://img.shields.io/codecov/c/github/goabonga/fastly-sync?logo=codecov)](https://codecov.io/gh/goabonga/fastly-sync)
[![PyPI](https://img.shields.io/pypi/v/fastly-sync.svg)](https://pypi.org/project/fastly-sync/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/goabonga/fastly-sync/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

`fastly-sync` reads an OpenAPI (`openapi.json`) document — from a local file or
a remote `http(s)` URL — and synchronises the derived **CDN** and **rate
limiter** configuration onto a [Fastly](https://www.fastly.com/) service, on
demand. Each path becomes a CDN cache setting whose policy is derived from its
methods (GET/HEAD-only paths are cached, paths with mutating methods pass) and
can be tuned with an `x-fastly-cache` extension; paths carrying an
`x-fastly-ratelimit` extension become Fastly rate limiters.

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

# Preview the changes without touching the live service:
fastly-sync sync --openapi ./openapi.json --dry-run

# Apply: clones the active version, configures cache settings and rate
# limiters, then activates the new version.
fastly-sync sync --openapi https://api.example.com/openapi.json
```

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
        "stale_if_error": 300
      }
    }
  }
}
```

`action` is `cache` or `pass` (defaults to the method-derived value); `ttl`
defaults to 3600s when caching; `stale_if_error` maps to Fastly's serve-stale
window (`stale_ttl`).

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
