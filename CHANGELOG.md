# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions and entries are computed from [Conventional Commits](https://www.conventionalcommits.org/)
by [multicz](https://github.com/goabonga/multicz).

## [0.1.0] - 2026-06-21

### Added

- synchronise Fastly CDN and rate limiter from an OpenAPI spec (`a35bac7`)
- derive CDN cache policy from methods and x-fastly-cache (`1451c4f`)
- scope CDN cache settings with per-path request conditions (`10efd2a`)
- apply stale-while-revalidate via a scoped Surrogate-Control header (`0a40d9d`)
- synchronise a WAF IP blocklist onto a Fastly Edge ACL (`83e4090`)
- export a Fastly Edge ACL back to the text blocklist format (`b0c774a`)
- add --only/--skip selectors to scope sync to cdn or ratelimit (`e828c9f`)
- prune managed CDN and rate-limiter objects removed from the spec (`2941b6e`)
- show a plan and confirm before applying (with --no-confirm) (`9af4d14`)
- add a show command for the live CDN, rate limiter and WAF config (`a902ec1`)
- add --output to show cdn and rate-limiter (`0316bda`)
- carry a CDN endpoint description via x-fastly-cache.description (`796687e`)

### Fixed

- anchor cache condition regex to prevent sibling path overlap (`729a06b`)

## [Unreleased]
