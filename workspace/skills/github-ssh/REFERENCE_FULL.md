---
name: github-ssh
description: Deterministic GitHub access with SSH-first transport and path-based HTTPS credential routing.
metadata:
  openclaw:
    emoji: "🔐"
    os: ["linux"]
    requires:
      bins: ["git", "ssh", "ssh-keygen", "rg"]
    primaryEnv: "github-ssh"
---

# GitHub Access (Deterministic)

## Primary path

Primary integration path is official GitHub MCP (container `ghcr.io/github/github-mcp-server`).

Use this skill as fallback-only when MCP is unavailable or in break-glass recovery.

Use this skill whenever a bot needs to clone, fetch, pull, push, or inspect GitHub repositories.

## Hard Rules

- Prefer SSH transport for all GitHub operations.
- HTTPS remotes are allowed when required; credential routing must stay deterministic.
- Before important GitHub operations, run preflight:

```bash
/home/aaron/.openclaw/scripts/github-ssh-preflight.sh
```

- If preflight fails, do not continue with repo mutation; report the exact failing check.

## Deterministic Auth Setup

These host-level settings are required and maintained globally:

- SSH host block for `github.com` in `~/.ssh/config`
- Key pinning to `/home/aaron/.ssh/id_rsa`
- Git URL rewrite from `http://github.com/...` to SSH
- Git credential helper router at `/home/aaron/.openclaw/scripts/github-credential-router.sh`
- `credential.useHttpPath=true` so helper can route by `ORG/REPO`

## Deterministic HTTPS Credential Routing

When Git uses HTTPS for `github.com`, profile selection is unified for bots:

- all bot workflows -> `rsl-bot` profile (`aaronclawrsl-bot` username)

Credential source of truth:

- `/home/aaron/.openclaw/credentials/github_credentials.json`

## Verified Commands

Connectivity/auth test:

```bash
ssh -T git@github.com -o BatchMode=yes -o ConnectTimeout=10
```

Remote reachability test:

```bash
git ls-remote https://github.com/aarwitz/lidi-solutions.git HEAD
```

Clone/pull examples:

```bash
git clone https://github.com/OWNER/REPO.git
git -C REPO pull --ff-only
```

## Failure Handling

If GitHub returns `Permission denied (publickey)`:

1. Confirm key exists and permissions are correct (`600` on private key).
2. Re-run preflight script.
3. If preflight auth still fails, the public key is not authorized in the GitHub account/org for that repo.

If Git HTTPS auth fails:

1. Confirm helper wiring (`git config --global --get credential.helper`).
2. Confirm path routing is enabled (`git config --global --get credential.useHttpPath`).
3. Validate helper output with `git credential fill` for the exact repo path.

## `gh` CLI Caveat — Bot Token Conflict

The shell environment has `GITHUB_TOKEN` set to the `aaronclawrsl-bot` token. This means `gh` CLI authenticates as the bot, not as `aarwitz`.

**Consequence**: `gh repo view aarwitz/lidi-solutions` (and any other personal `aarwitz/*` repo) will fail because the bot does not have access to Aaron's personal repos.

**Rule**: For any operation on personal `aarwitz/*` repos, use `git` over SSH (which uses `~/.ssh/id_rsa`) — do **not** use `gh` CLI commands. SSH transport works correctly regardless of the `GITHUB_TOKEN` env var.

## Mandatory `gh` Wrapper

All `gh` commands must run through the router so account selection is deterministic by agent:

```bash
/home/aaron/.openclaw/scripts/gh-account-router.sh --agent <main|resi|dwight|druck> <gh args...>
```

Examples:

```bash
# Jerry/Dwight/Druck/Resi -> aaronclawrsl-bot
/home/aaron/.openclaw/scripts/gh-account-router.sh --agent main pr create --fill

# Resi -> aaronclawrsl-bot
/home/aaron/.openclaw/scripts/gh-account-router.sh --agent resi pr list --repo EWAG-dev/iosApp
```

Never call raw `gh ...` for mutations from bot workflows.

```bash
# Works (SSH):
git ls-remote git@github.com:aarwitz/lidi-solutions.git HEAD
git -C /path/to/repo pull --ff-only

# Does NOT work for personal repos (bot token active):
gh repo view aarwitz/lidi-solutions
```

## Scope

This skill is intended to be present for all bots (existing and future) to keep GitHub access reliable and uniform.

Status: fallback-only while official GitHub MCP is promoted.
