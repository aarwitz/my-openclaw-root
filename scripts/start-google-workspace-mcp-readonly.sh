#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Official-ish MCP path for Google Workspace (community-maintained package).
# Read-only mode is enforced to reduce blast radius during rollout.

"/home/aaron/.openclaw/scripts/ensure-google-workspace-mcp-credentials.sh"

exec npx -y google-workspace-mcp serve --read-only
