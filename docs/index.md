# fastly-sync

`fastly-sync` reads an OpenAPI (`openapi.json`) document — local or remote —
and synchronises the derived CDN and rate limiter configuration onto a Fastly
service, on demand.

## Install

```bash
pip install fastly-sync
```

## Usage

```bash
export FASTLY_API_TOKEN=...
export FASTLY_SERVICE_ID=...

fastly-sync sync --openapi ./openapi.json --dry-run
fastly-sync sync --openapi https://api.example.com/openapi.json
```

Each path in the spec becomes a CDN cache setting: GET/HEAD-only paths are
cached, paths with mutating methods pass, and the policy can be tuned per path
with an `x-fastly-cache` extension (`action`, `ttl`, `stale_while_revalidate`,
`stale_if_error`). A path carrying an `x-fastly-ratelimit` extension
(`{ "name": ..., "limit": N, "window": S }`) becomes a Fastly rate limiter.

See the navigation for the stability and deprecation policy.
