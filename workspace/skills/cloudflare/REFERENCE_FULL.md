---
name: cloudflare
description: Manage Cloudflare account operations for redstonelabs.us and lidisolutions.ai including DNS, Pages, token verification, and safe migration/decommission workflows without leaking secrets.
metadata: {"clawdbot":{"emoji":"☁️"}}
---

# Cloudflare Skill

Use this skill when the task involves Cloudflare account operations.

## Ownership

- Primary operator: Jerry
- Domains in scope:
  - `redstonelabs.us`
  - `lidisolutions.ai`

## Source Of Truth

- Operational doc: `workspace/openclaw-config/CLOUDFLARE_OFFICIAL.md`
- LIDI project handoff: `workspace/lidi-solutions/AGENT_HANDOFF.md`

## Active Token

- Name: `icy-sun-9031`
- Scope: Account-level (`6729a939101c819b5a656b06c3bb0d0b`) — broad permissions including DNS, Workers, R2, Pages, Tunnels, Zero Trust, AI Gateway, D1, KV, Queues, Images, and more
- Expires: 2027-05-10

## Secret Handling

Never place raw Cloudflare tokens in markdown, commits, or chat outputs.

Read secrets at runtime from:
- `~/.openclaw/credentials/cloudflare/account-token`
- `~/.openclaw/credentials/cloudflare/account-meta.json`

## Standard Runtime Setup

```bash
export CLOUDFLARE_API_TOKEN="$(cat ~/.openclaw/credentials/cloudflare/account-token)"
export CLOUDFLARE_ACCOUNT_ID="6729a939101c819b5a656b06c3bb0d0b"
```

## Verify Token

```bash
curl -sS -X GET "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/tokens/verify" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}"
```

## DNS Operations Pattern

1. Confirm target zone and existing records.
2. Apply minimal record changes.
3. Re-verify resolution with `dig`.
4. Do not proxy non-HTTP services (mail/FTP).

## Pages Operations Pattern

1. Ensure build command and output dir are correct (`npm run build`, `dist`).
2. Validate custom domain bindings.
3. Purge cache only when needed after deploy.

## Legacy Tunnel Rule

- `vision-app.redstonelabs.us` tunnel is decommissioned.
- Do not restore old docker tunnel commands unless explicitly requested.

## Safety Rules

1. Keep permissions at minimum required scope whenever possible.
2. Rotate/revoke stale tokens and stale tunnel artifacts.
3. Use auditable commands and record outcomes in the relevant handoff doc.