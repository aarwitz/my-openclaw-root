# cli_guide.md


Commands from bringing up and down openclaw on my ubuntu PC so I can use it on telegram
## Overview

This system runs:

OpenClaw (gateway)
+ systemd (process management)
+ Tailscale (secure access)
+ Telegram (interface)
+ Google (optional tools)

Access model:

Localhost only (127.0.0.1)
→ exposed via Tailscale serve
→ controlled via Telegram (paired users only)

Note:
- This guide uses external Tailscale serve in front of a local OpenClaw gateway.
- Keep `gateway.tailscale.mode` set to `off` in OpenClaw config for this model.

---

# 🟢 Start OpenClaw (full system)

## 1. Ensure Node is correct (IMPORTANT)

/usr/bin/node --version

Expected: Node 22.14+ (22.x LTS is preferred).

Note:
- Gateway service should use system Node at `/usr/bin/node`.
- Your interactive shell may still resolve `node` from nvm; that is fine for ad-hoc CLI usage.

---

## 2. Start OpenClaw gateway

### If running manually:
openclaw gateway run --force

### If using systemd:
openclaw gateway start

Check status:
openclaw gateway status

---

## 3. Start Tailscale (if not already running)

sudo tailscale up

---

## 4. Expose OpenClaw via Tailscale

tailscale serve --bg --http 80 http://127.0.0.1:18789

Verify:
tailscale serve status

Expected proxy target:
http://127.0.0.1:18789

---

## 5. Access dashboard

openclaw dashboard --no-open

Open the URL shown:
http://127.0.0.1:18789/#token=...

---

## 6. Verify system

### Telegram test:
hello

### Health check:
openclaw health

---

# 🔴 Stop OpenClaw

## Stop gateway

### Manual:
pkill -f 'openclaw.*gateway'

### systemd:
openclaw gateway stop

---

## Stop Tailscale serve (exposure)

tailscale serve --http=80 off

---

## (Optional) Stop Tailscale entirely

sudo tailscale down

---

## Stop background heartbeat
openclaw config set agents.defaults.heartbeat.every "0m"
bash /home/aaron/.openclaw/scripts/safe-restart.sh

## Turn background heartbeat back on
openclaw config set agents.defaults.heartbeat.every "4h"
bash /home/aaron/.openclaw/scripts/safe-restart.sh

# 🔄 Restart OpenClaw

Use token-safe restart only:
bash /home/aaron/.openclaw/scripts/safe-restart.sh

---

# 🔍 Debugging

## Check logs
openclaw logs

## Check health
openclaw health

## Check Tailscale
tailscale status
tailscale serve status

---

# 🔐 Pairing (Telegram access)

openclaw pairing approve telegram <PAIRING_CODE>

---

# 📬 Google integration (optional)

gog auth list

gog gmail search 'newer_than:2d' --max 5

---

# ⚠️ Common issues

## Web UI says "disconnected"
bash /home/aaron/.openclaw/scripts/safe-restart.sh

## Telegram not responding
- Ensure bot was started (/start)
- Ensure pairing completed

## Port mismatch
OpenClaw → 127.0.0.1:18789
Tailscale → proxies to same port

Fix mismatch quickly:
tailscale serve --http=80 off
tailscale serve --bg --http 80 http://127.0.0.1:18789
tailscale serve status

## Wrong Node version
/usr/bin/node --version

If this is below 22.14, reinstall system Node 22 LTS and run:
openclaw doctor --fix

---

# 🧠 Mental model

OpenClaw = brain
systemd = keeps it alive
Tailscale = private network tunnel
Telegram = UI

---

# ✅ Minimal startup (TL;DR)

openclaw gateway run --force
tailscale serve --bg --http 80 http://127.0.0.1:18789

---

# ✅ Minimal shutdown

pkill -f 'openclaw.*gateway'
tailscale serve --http=80 off
