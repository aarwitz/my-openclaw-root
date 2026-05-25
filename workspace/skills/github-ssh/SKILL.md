---
name: github-ssh
description: Deterministic GitHub router with SSH-first transport and explicit HTTPS credential routing fallback.
metadata:
  openclaw:
    os: ["linux"]
    requires:
      bins: ["git", "ssh", "ssh-keygen", "rg"]
    primaryEnv: "github-ssh"
---

# GitHub SSH Router (Lean + Deterministic)

Primary path is official GitHub MCP.
Use this skill only when MCP is unavailable or for break-glass git auth recovery.

## Operation Table

| Operation | Deterministic Action | Fail-Closed Rule |
|---|---|---|
| Preflight | run `/home/aaron/.openclaw/scripts/github-ssh-preflight.sh` | if any check fails, stop before mutation |
| Repo transport | prefer `git@github.com:ORG/REPO.git` SSH remotes | if SSH blocked, use HTTPS with router only |
| HTTPS credential routing | use `credential.helper` router + `credential.useHttpPath=true` | if routing ambiguous, stop and report |
| CLI mutations | run `gh` only via `/home/aaron/.openclaw/scripts/gh-account-router.sh --agent ...` | never run raw `gh` mutation commands |

## Hard Rules

- Never output credentials or tokens.
- For personal `aarwitz/*` repositories, do not use raw `gh` when bot token is active; use git over SSH.
- Do not continue after preflight failure.
- Keep auth/account selection deterministic by path or agent router.

## Deterministic Routing

- SSH is default for GitHub repository operations.
- HTTPS fallback must use helper: `/home/aaron/.openclaw/scripts/github-credential-router.sh`.
- `credential.useHttpPath=true` is required so `ORG/REPO` maps to the right profile.
- Agent-based `gh` wrapper is mandatory for mutations.

## Output Contract

Return:
1. selected transport/auth route
2. preflight outcome
3. executed command class (read-only or mutation)
4. any auth/routing discrepancy and exact failing check
5. safe next action

## On-Demand Deep Reference

- `/home/aaron/.openclaw/workspace/skills/github-ssh/REFERENCE_FULL.md`
