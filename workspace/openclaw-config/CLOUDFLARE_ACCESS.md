# Cloudflare Access Notes (Legacy Summary)

This file is a lightweight legacy pointer only.

## Current Setup (2026-05-10)

- Cloudflare Tunnel (`vision-app.redstonelabs.us`) is decommissioned.
- The mini app is served via Tailscale VPN at `http://rsl:8000`.
- Mini app path: `/static/miniapp.html?group=telegram:-4995650408`.

## Official Source Of Truth

- Use `workspace/openclaw-config/CLOUDFLARE_OFFICIAL.md` for all active Cloudflare operations.

## Secret Handling Rule

- Do not store raw API tokens in workspace docs.
- Store Cloudflare secrets only in `~/.openclaw/credentials/cloudflare/`.
