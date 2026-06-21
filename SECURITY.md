# Security Policy

## Supported versions

Security fixes are applied only to the latest released version on the
`main` branch (and the matching release of `fastly-sync`).

| Version | Supported |
| --- | --- |
| latest release | ✅ |
| older releases | ❌ |

## Reporting a vulnerability

**Please do not open a public issue.** GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
is the preferred channel:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the issue with reproduction steps and a suggested mitigation.

If you cannot use GitHub's form, email **goabonga@pm.me** with the same
information. PGP encryption is available on request.

You can expect an acknowledgement within **3 business days**, a triage
assessment within **10 business days**, and a fix or written mitigation
plan before any public disclosure.

## Scope

`fastly-sync` reads declarative sources (an OpenAPI document and/or an IP
blocklist, local or remote) and pushes the derived CDN, rate limiter and WAF
Edge ACL configuration to the Fastly API. The parts most relevant to security
are:

- handling of the **Fastly API token** (`FASTLY_API_TOKEN`) — never logged,
  never written to disk;
- fetching **remote OpenAPI documents and IP blocklists** over `http(s)`
  (untrusted input parsed as JSON / validated as IP/CIDR);
- the **changes pushed to a live Fastly service** (including the WAF IP
  blocklist) — review `--dry-run` output before applying.

Vulnerabilities in third-party dependencies should be reported upstream, but
please let us know so the pinned ranges can be bumped.

Thanks for helping keep the project and its users safe.
