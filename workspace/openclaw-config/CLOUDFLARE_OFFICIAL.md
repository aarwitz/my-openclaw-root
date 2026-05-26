# Cloudflare Official Operations Doc

## Scope

This is the official Cloudflare operations reference for Jerry.

Managed domains and projects:
- `redstonelabs.us`
- `lidisolutions.ai`
- Legacy repo reference: `https://github.com/aarwitz/RedstoneLabs.git`

## Account

- Login email: `aaronhorowits97@gmail.com`
- Account ID: `6729a939101c819b5a656b06c3bb0d0b`
- API token name: `icy-sun-9031` (replaced `red-limit-446d` 2026-05-10)
- API token expiration: `2027-05-10T23:59:59Z`
- Token scope: Account-level — broad permissions (DNS, Workers, R2, Pages, Tunnels, ZeroTrust, D1, KV, etc.)
- **Missing**: `account.zone.create` — cannot add new zones via API; must be done in CF dashboard

## Credential Storage (Do Not Inline Secrets)

- Cloudflare API token file: `~/.openclaw/credentials/cloudflare/account-token`
- Cloudflare Worker deploy token file: `~/.openclaw/credentials/cloudflare/account-token.bak`
- Cloudflare account metadata: `~/.openclaw/credentials/cloudflare/account-meta.json`
- Cloudflare router script: `/home/aaron/.openclaw/scripts/cloudflare-account-router.sh`
- Origin cert (redstonelabs): `~/.openclaw/credentials/cloudflare/redstonelabs-origin-cert.pem`
- Origin key (redstonelabs): `~/.openclaw/credentials/cloudflare/redstonelabs-origin-key.pem`

Permissions on credential files must remain `600`.

## Current State

- Cloudflare tunnel `vision-app.redstonelabs.us` is decommissioned.
- `cloudflared` binary is installed at `/usr/bin/cloudflared` and symlinked from `/usr/local/bin/cloudflared`.
- No active local cloudflared config was found under `~/.cloudflared/`.
- As of `2026-05-26`, the nominal active token at `account-token` verifies as active but fails Cloudflare Worker service mutation calls with auth error `10000`.
- As of `2026-05-26`, Worker publish for `tm.lidisolutions.ai` succeeded only with `~/.openclaw/credentials/cloudflare/account-token.bak`.
- Treat token routing as split until fixed:
  - `account-token.bak` for Worker deploy mutations
  - `account-token` may still work for some read/D1 operations, but do not trust it for Worker publish
- Use `/home/aaron/.openclaw/scripts/cloudflare-account-router.sh` instead of exporting token files manually.

## Cleanup Completed (2026-05-10)

- Removed stale command file: `~/Documents/Cloudflare/docker_run_cloudflared_cmd`
- Removed plaintext cert/key bundle: `~/Documents/Cloudflare/keytemps.txt`
- Migrated cert/key into restricted credential files under `~/.openclaw/credentials/cloudflare/`

## Verification Commands

```bash
/home/aaron/.openclaw/scripts/cloudflare-account-router.sh --mode default --verify
/home/aaron/.openclaw/scripts/cloudflare-account-router.sh --mode worker-mutate --print-token-path
/home/aaron/.openclaw/scripts/cloudflare-account-router.sh --mode auto --print-token-path
```

## Known Blockers (2026-05-10)

### lidisolutions.ai Cloudflare Pages Migration — Aaron must do manually

1. **Add zone in CF dashboard**: `lidisolutions.ai` cannot be added via API (token lacks `account.zone.create`). Go to CF dashboard → Add a Site.
2. **Fix Pages GitHub integration**: CF Pages Git installation is broken on Cloudflare's side. Go to CF dashboard → Pages → Settings → Git Integration → Reinstall GitHub App.
3. Once both are done, Jerry can complete via API:
   - Create Pages project targeting `aarwitz/lidi-solutions`
   - Build command: `npm run build`, publish dir: `dist`
   - Configure DNS CNAME for `lidisolutions.ai` → Pages domain
   - Verify `/solutions` and `/solutions/hello_world` routes

## Jerry Operating Rules

1. Never put Cloudflare tokens in markdown, git commits, or chat summaries.
2. Use account token from credential file at runtime.
3. For `lidisolutions.ai`, prioritize Cloudflare Pages + DNS migration path.
4. For `redstonelabs.us`, treat old tunnel/docker commands as deprecated unless explicitly re-approved.
5. If decommissioning `cloudflared` binary, use privileged manual command:

```bash
sudo rm -f /usr/local/bin/cloudflared /usr/bin/cloudflared
```

Only run removal when confirmed no host depends on Cloudflare Tunnel.
