#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: setup-google-workspace-mcp.sh --account <name>

Runs one-time Google Workspace MCP auth setup for a named account.
This opens a browser OAuth flow.

Examples:
  /home/aaron/.openclaw/scripts/setup-google-workspace-mcp.sh --account main
  /home/aaron/.openclaw/scripts/setup-google-workspace-mcp.sh --account resi
EOF
}

account=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --account)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      account="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$account" ]]; then
  usage
  exit 2
fi

"/home/aaron/.openclaw/scripts/ensure-google-workspace-mcp-credentials.sh"

npx -y google-workspace-mcp accounts add "$account"
npx -y google-workspace-mcp accounts test-permissions "$account"
