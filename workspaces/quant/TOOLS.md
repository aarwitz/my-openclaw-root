# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

## Related

- [Agent workspace](/concepts/agent-workspace)


## Scripts policy (shared across all OpenClaw agents)

All `.sh`/`.py` scripts under `~/.openclaw/scripts/` and registered workspace script dirs are governed by a single policy. Source of truth: `~/.openclaw/scripts/README.md`.

Rules for you:
- Run scripts via `~/.openclaw/scripts/run-with-trace.sh <script> [args...]` so the call is logged to `~/.openclaw/logs/script-runs.jsonl`. Direct invocation auto-reroutes through the wrapper by default; with `OPENCLAW_REQUIRE_WRAPPER_NO_AUTORUN=1` it exits `126`.
- Create new scripts with `~/.openclaw/scripts/new-script.sh <name>.{sh,py}` — never hand-write boilerplate.
- Before retiring a script, run `~/.openclaw/scripts/scripts-policy-lint.sh` and the inventory audit, and follow the deletion rule in the README.
