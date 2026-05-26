#!/usr/bin/env bash
set -euo pipefail

# Dwight assignment wrapper: sets owner explicitly and delegates routing/execution.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$SCRIPT_DIR/launch-coding-task.sh"

owner_agent=""

auto_scope="medium"
auto_expected_files="1"
auto_risk="low"
auto_heavy_tag="false"
auto_acp_available="false"
auto_acp_agent=""
auto_agent_timeout="300"

task_id=""
repo_path=""
goals=""
acceptance=""
execute="false"

usage() {
  cat <<'EOF'
Usage:
  dwight-assign-coding-task.sh --owner-agent <id> --task-id <id> --repo <abs-path> --goal <text> [options]

Required:
  --owner-agent <id>      OpenClaw owner agent (for example: resi, main)
  --task-id <id>          Stable task identifier
  --repo <abs-path>       Absolute repo path
  --goal <text>           Task objective

Optional:
  --acceptance <text>
  --scope <low|medium|high>        Default: medium
  --expected-files <int>           Default: 1
  --risk <low|medium|high>         Default: low
  --tag-heavy <true|false>         Default: false
  --acp-available <true|false>     Default: false
  --acp-agent <id>                 Optional ACP harness override
  --agent-timeout <seconds>        openclaw agent timeout. Default: 300
  --execute                         Actually launch (default dry-run)
  --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner-agent)
      owner_agent="${2:-}"
      shift 2
      ;;
    --task-id)
      task_id="${2:-}"
      shift 2
      ;;
    --repo)
      repo_path="${2:-}"
      shift 2
      ;;
    --goal)
      goals="${2:-}"
      shift 2
      ;;
    --acceptance)
      acceptance="${2:-}"
      shift 2
      ;;
    --scope)
      auto_scope="${2:-}"
      shift 2
      ;;
    --expected-files)
      auto_expected_files="${2:-}"
      shift 2
      ;;
    --risk)
      auto_risk="${2:-}"
      shift 2
      ;;
    --tag-heavy)
      auto_heavy_tag="${2:-}"
      shift 2
      ;;
    --acp-available)
      auto_acp_available="${2:-}"
      shift 2
      ;;
    --acp-agent)
      auto_acp_agent="${2:-}"
      shift 2
      ;;
    --agent-timeout)
      auto_agent_timeout="${2:-}"
      shift 2
      ;;
    --execute)
      execute="true"
      shift 1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$owner_agent" || -z "$task_id" || -z "$repo_path" || -z "$goals" ]]; then
  echo "Missing required arguments." >&2
  usage >&2
  exit 2
fi

cmd=(
  "$LAUNCHER"
  --owner-agent "$owner_agent"
  --task-id "$task_id"
  --repo "$repo_path"
  --goal "$goals"
  --scope "$auto_scope"
  --expected-files "$auto_expected_files"
  --risk "$auto_risk"
  --tag-heavy "$auto_heavy_tag"
  --acp-available "$auto_acp_available"
  --agent-timeout "$auto_agent_timeout"
)

if [[ -n "$auto_acp_agent" ]]; then
  cmd+=(--acp-agent "$auto_acp_agent")
fi

if [[ -n "$acceptance" ]]; then
  cmd+=(--acceptance "$acceptance")
fi

if [[ "$execute" == "true" ]]; then
  cmd+=(--execute)
fi

"${cmd[@]}"
