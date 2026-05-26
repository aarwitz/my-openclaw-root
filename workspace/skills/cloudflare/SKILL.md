---
name: cloudflare
description: Deterministic Cloudflare router for DNS/Pages/account operations with strict secret-safety and minimal-change workflows.
metadata: {"clawdbot":{"emoji":"☁️"}}
---

# Cloudflare Router (Lean + Deterministic)

Use this skill for Cloudflare DNS, Pages, token verification, and controlled migration/decommission actions.

## Operation Table

| Operation | Deterministic Action | Fail-Closed Rule |
|---|---|---|
| Token verify | call account token verify endpoint | if verify fails, stop privileged operations |
| DNS change | inspect current records, apply minimal diff, re-check with `dig` | no broad/unsafe record churn |
| Pages update | validate build/output + domain binding | no blind cache purge unless needed |
| Legacy cleanup | keep decommissioned tunnel retired unless explicitly requested | do not restore legacy tunnel by default |

## Scope

- Domains: `redstonelabs.us`, `lidisolutions.ai`
- Account-level operations only when required by task

## Hard Rules

- Never expose raw tokens in markdown, commits, or responses.
- Read secrets only at runtime from credential files.
- Keep permissions minimal where possible.
- Prefer auditable commands and minimal diffs.

## Runtime Inputs

- token file: `~/.openclaw/credentials/cloudflare/account-token`
- worker deploy token file: `~/.openclaw/credentials/cloudflare/account-token.bak`
- metadata file: `~/.openclaw/credentials/cloudflare/account-meta.json`

## Mandatory Wrapper

Use the router instead of exporting token files manually:

```bash
/home/aaron/.openclaw/scripts/cloudflare-account-router.sh --mode auto -- <command ...>
```

Useful checks:

```bash
/home/aaron/.openclaw/scripts/cloudflare-account-router.sh --mode default --verify
/home/aaron/.openclaw/scripts/cloudflare-account-router.sh --mode worker-mutate --print-token-path
/home/aaron/.openclaw/scripts/cloudflare-account-router.sh --mode auto --print-token-path
```

`auto` selects:
- `account-token.bak` for known Wrangler Worker mutation commands
- `account-token` for default/read operations

## Output Contract

Return:
1. operation executed
2. zone/domain affected
3. verification evidence (token/api/dns/pages)
4. rollback or next-safe action

## On-Demand Deep Reference

- `workspace/skills/cloudflare/REFERENCE_FULL.md`
