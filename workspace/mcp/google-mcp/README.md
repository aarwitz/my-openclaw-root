# Google Workspace MCP strategy

Current state:
- Gmail/Drive/Calendar/Sheets/Docs operations run through gog skills and scripts.
- This is not official Google MCP.

## Official status reality

There is currently no single canonical Google-published MCP server equivalent to GitHub's official github-mcp-server for full Workspace coverage.

Practical guidance:
- Keep gog as production path today.
- Migrate to MCP only after selecting one stable Workspace MCP server and validating auth/session behavior under your real workloads.

## Deterministic migration plan

1. Pick one Workspace MCP server and pin an exact version.
2. Run it in read-only mode first (list/search/read only).
3. Add contract/smoke checks similar to task-manager-mcp.
4. Test Gmail send, Drive upload, and Sheets write in a staging workflow.
5. After 14 days of clean operation, demote gog scripts to fallback-only.

## Decommission criteria for gog scripts

Only remove gog script paths after all are true:
- 14 consecutive days of successful MCP read/write operations
- zero auth drift incidents in production
- successful retry behavior under token refresh failures
- deterministic account mapping tested for each agent

## Why not delete scripts now

Immediate deletion would remove your only known-stable path for Google operations and likely reduce reliability.
Use staged replacement, then remove fallback.
