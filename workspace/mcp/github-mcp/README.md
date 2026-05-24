# GitHub MCP (official)

This folder provides official GitHub MCP configuration using:
- Container image: ghcr.io/github/github-mcp-server

Important:
- The old npm package @modelcontextprotocol/server-github is deprecated.
- Use the official GitHub MCP server container instead.

## Config file

- mcp-client.official-all-agents.json

Active VS Code workspace MCP config now wired at:

```bash
/home/aaron/.openclaw/.vscode/mcp.json
```

This config launches GitHub MCP via:

```bash
/home/aaron/.openclaw/scripts/start-github-mcp-official.sh --agent <main|resi|dwight|druck>
```

The config uses explicit stdio mode and default toolsets.

It defines one GitHub MCP server entry per agent so each can use a separate PAT.

## Environment variables expected

- GITHUB_PAT_MAIN
- GITHUB_PAT_RESI
- GITHUB_PAT_DWIGHT
- GITHUB_PAT_DRUCK

Generate these env vars from your existing credential profiles:

```bash
source <(/home/aaron/.openclaw/scripts/export-github-mcp-env.sh)
```

Quick validation:

```bash
/home/aaron/.openclaw/scripts/validate-github-mcp-official.sh --quick
```

Full per-agent identity validation matrix:

```bash
/home/aaron/.openclaw/scripts/validate-github-mcp-official.sh
```

## Rollout

1. Start with read-only scopes and read-only tools where possible.
2. Validate issue and PR read/list operations for each agent account.
3. Enable write operations only after per-agent account validation passes.
4. Keep github-ssh and account-router scripts as fallback until MCP parity is proven.

## Fallback-only policy (legacy scripts)

- `github-ssh` skill path is now fallback-only for break-glass scenarios.
- `gh-account-router.sh` remains a compatibility fallback while MCP stabilizes.
- New automations should target official GitHub MCP first.

## Decommission criteria for GitHub scripts

Only remove legacy script paths after all are true:
- 14 consecutive days of successful GitHub MCP operations for all agents
- zero account-routing incidents
- zero token/auth drift incidents that required script fallback
- preflight checks updated to MCP checks and passing
