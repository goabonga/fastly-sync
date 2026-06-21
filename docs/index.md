# fastly-sync

`fastly-sync` synchronises Fastly configuration on demand: it reads an OpenAPI
(`openapi.json`) document (local or remote) to drive the CDN cache and rate
limiters, and a text blocklist to drive WAF IP blacklisting (an Edge ACL).

## Install

```bash
pip install fastly-sync
```

## Usage

```bash
export FASTLY_API_TOKEN=...
export FASTLY_SERVICE_ID=...

# Everything: CDN cache + rate limiters AND the WAF blocklist:
fastly-sync sync all --openapi ./openapi.json --blocklist ./blocklist.txt
fastly-sync sync all --openapi ./openapi.json --blocklist ./blocklist.txt --dry-run

# A single target:
fastly-sync sync cdn          --openapi ./openapi.json
fastly-sync sync rate-limiter --openapi ./openapi.json
fastly-sync sync waf          --blocklist ./blocklist.txt

# WAF IP blacklisting from a text blocklist (one IP/CIDR per line):
fastly-sync sync waf --blocklist ./blocklist.txt --bootstrap
fastly-sync sync waf --blocklist ./blocklist.txt --dry-run

# Show the live config applied on Fastly, per target:
fastly-sync show all
fastly-sync show waf --output blocklist.txt   # export the blocklist
```

`sync` is declarative: by default it also prunes managed objects no longer in
the spec (`--no-prune` to keep additive). It prints a plan and asks for
confirmation before applying (`--no-confirm` to skip).

Each path in the spec becomes a CDN cache setting: GET/HEAD-only paths are
cached, paths with mutating methods pass, and the policy can be tuned per path
with an `x-fastly-cache` extension (`action`, `ttl`, `stale_while_revalidate`,
`stale_if_error`). A path carrying an `x-fastly-ratelimit` extension
(`{ "name": ..., "limit": N, "window": S }`) becomes a Fastly rate limiter.

See the navigation for the stability and deprecation policy.
