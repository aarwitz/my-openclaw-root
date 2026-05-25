#!/usr/bin/env bash
set -euo pipefail

# Lane-aware coding task launcher for hybrid inline/Codex-subagent/ACP architecture.
# Default mode is dry-run so operators can inspect routing before execution.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTER="$SCRIPT_DIR/select-coding-lane.sh"
RUNS_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}/tmp/coding-lane-runs"

orchestrator_agent="dwight"
coder_codex_agent="main"
owner_agent=""
acp_agent="cursor"
acp_agent_overridden="false"
task_id=""
repo_path=""
goals=""
acceptance=""
scope="medium"
expected_files="1"
risk="low"
heavy_tag="false"
acp_available="false"
run_timeout="1800"
agent_timeout="120"
execute="false"
requested_lane=""
selected_lane=""
fallback_applied="false"
fallback_reason=""
acp_preflight_status="not-run"
acp_preflight_detail=""

should_trigger_acp_runtime_fallback() {
  local output="$1"
  if echo "$output" | grep -Eq 'Failed to spawn agent command|spawn [^ ]+ ENOENT|AcpRuntimeError|Permission prompt unavailable in non-interactive mode'; then
    return 0
  fi
  return 1
}

resolve_openclaw_bin() {
  local candidate=""

  if command -v openclaw >/dev/null 2>&1; then
    command -v openclaw
    return 0
  fi

  for candidate in \
    "$HOME/.nvm/versions/node/v22.22.1/bin/openclaw" \
    "$HOME/.local/bin/openclaw" \
    "/usr/local/bin/openclaw" \
    "/usr/bin/openclaw"
  do
    if [[ -x "$candidate" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  return 1
}

OPENCLAW_BIN="${OPENCLAW_BIN:-}"
if [[ -z "$OPENCLAW_BIN" ]]; then
  OPENCLAW_BIN="$(resolve_openclaw_bin || true)"
fi

usage() {
  cat <<'EOF'
Usage:
  launch-coding-task.sh --task-id <id> --repo <abs-path> --goal <text> [options]

Required:
  --task-id <id>          Stable task identifier.
  --repo <abs-path>       Absolute repo path for coding work.
  --goal <text>           Task objective.

Routing inputs:
  --scope <low|medium|high>      Default: medium
  --expected-files <int>         Default: 1
  --risk <low|medium|high>       Default: low
  --tag-heavy <true|false>       Default: false
  --acp-available <true|false>   Default: false

Execution options:
  --acceptance <text>            Optional acceptance criteria.
  --orchestrator-agent <id>      Default: dwight
  --owner-agent <id>             Optional assignee (for example: resi or main)
  --coder-codex-agent <id>       Default: main (Jerry)
  --acp-agent <id>               Default: cursor
  --timeout <seconds>            ACP run timeout hint. Default: 1800
  --agent-timeout <seconds>      openclaw agent timeout. Default: 120
  --execute                      Actually invoke OpenClaw command (default is dry-run)
  --help

Examples:
  launch-coding-task.sh \
    --task-id TM-421 \
    --repo /home/aaron/repos/lidi-task-manager \
    --goal "Fix OAuth callback race" \
    --acceptance "All auth tests green" \
    --scope high --expected-files 12 --risk high --tag-heavy true

  launch-coding-task.sh \
    --task-id TM-422 \
    --repo /home/aaron/repos/lidi-task-manager \
    --goal "Typo fix in CLI output" \
    --scope low --expected-files 1 --risk low --acp-available false \
    --execute
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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
      scope="${2:-}"
      shift 2
      ;;
    --expected-files)
      expected_files="${2:-}"
      shift 2
      ;;
    --risk)
      risk="${2:-}"
      shift 2
      ;;
    --tag-heavy)
      heavy_tag="${2:-}"
      shift 2
      ;;
    --acp-available)
      acp_available="${2:-}"
      shift 2
      ;;
    --orchestrator-agent)
      orchestrator_agent="${2:-}"
      shift 2
      ;;
    --owner-agent)
      owner_agent="${2:-}"
      shift 2
      ;;
    --coder-codex-agent)
      coder_codex_agent="${2:-}"
      shift 2
      ;;
    --acp-agent)
      acp_agent="${2:-}"
      acp_agent_overridden="true"
      shift 2
      ;;
    --timeout)
      run_timeout="${2:-}"
      shift 2
      ;;
    --agent-timeout)
      agent_timeout="${2:-}"
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

if [[ -z "$task_id" || -z "$repo_path" || -z "$goals" ]]; then
  echo "Missing required arguments: --task-id, --repo, --goal" >&2
  usage >&2
  exit 2
fi

if [[ "$repo_path" != /* ]]; then
  echo "--repo must be an absolute path: $repo_path" >&2
  exit 2
fi

if [[ ! -d "$repo_path" ]]; then
  echo "Repo path not found: $repo_path" >&2
  exit 2
fi

if ! [[ "$scope" =~ ^(low|medium|high)$ ]]; then
  echo "Invalid --scope: $scope" >&2
  exit 2
fi

if ! [[ "$risk" =~ ^(low|medium|high)$ ]]; then
  echo "Invalid --risk: $risk" >&2
  exit 2
fi

if ! [[ "$expected_files" =~ ^[0-9]+$ ]]; then
  echo "Invalid --expected-files: $expected_files" >&2
  exit 2
fi

if ! [[ "$run_timeout" =~ ^[0-9]+$ ]]; then
  echo "Invalid --timeout: $run_timeout" >&2
  exit 2
fi

if ! [[ "$agent_timeout" =~ ^[0-9]+$ ]]; then
  echo "Invalid --agent-timeout: $agent_timeout" >&2
  exit 2
fi

if ! [[ "$heavy_tag" =~ ^(true|false)$ ]]; then
  echo "Invalid --tag-heavy: $heavy_tag" >&2
  exit 2
fi

if ! [[ "$acp_available" =~ ^(true|false)$ ]]; then
  echo "Invalid --acp-available: $acp_available" >&2
  exit 2
fi

if [[ ! -x "$ROUTER" ]]; then
  echo "Routing helper missing or not executable: $ROUTER" >&2
  exit 2
fi

get_config_value() {
  local key="$1"
  local val=""
  if [[ -z "$OPENCLAW_BIN" || ! -x "$OPENCLAW_BIN" ]]; then
    return 1
  fi
  val="$("$OPENCLAW_BIN" config get "$key" 2>/dev/null || true)"
  if [[ -z "$val" ]]; then
    return 1
  fi
  val="$(echo "$val" | tr -d '[:space:]')"
  val="${val//\"/}"
  printf '%s' "$val"
}

get_config_json() {
  local key="$1"
  if [[ -z "$OPENCLAW_BIN" || ! -x "$OPENCLAW_BIN" ]]; then
    return 1
  fi
  "$OPENCLAW_BIN" config get "$key" --json 2>/dev/null || return 1
}

acp_agent_command_available() {
  local agent="$1"
  case "$agent" in
    cursor)
      command -v cursor-agent >/dev/null 2>&1
      ;;
    claude)
      command -v claude >/dev/null 2>&1
      ;;
    copilot)
      command -v copilot >/dev/null 2>&1
      ;;
    codex)
      command -v codex >/dev/null 2>&1
      ;;
    *)
      # Unknown harness ids are allowed if configured by OpenClaw.
      return 0
      ;;
  esac
}

default_acp_agent_for_owner() {
  local owner="$1"
  local -a preferred=()
  local candidate

  case "$owner" in
    resi)
      preferred=(claude cursor copilot)
      ;;
    main|jerry|dwight)
      preferred=(cursor copilot claude)
      ;;
    druck)
      preferred=(copilot cursor claude)
      ;;
    *)
      preferred=(cursor copilot claude)
      ;;
  esac

  for candidate in "${preferred[@]}"; do
    if acp_agent_command_available "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  printf '%s' "${preferred[0]}"
}

acp_preflight_check() {
  local permission_mode=""
  local non_interactive=""
  local acp_enabled=""
  local acpx_enabled=""
  local allowed_agents_json=""

  permission_mode="$(get_config_value "plugins.entries.acpx.config.permissionMode" || true)"
  non_interactive="$(get_config_value "plugins.entries.acpx.config.nonInteractivePermissions" || true)"
  acp_enabled="$(get_config_value "acp.enabled" || true)"
  acpx_enabled="$(get_config_value "plugins.entries.acpx.enabled" || true)"
  allowed_agents_json="$(get_config_json "acp.allowedAgents" || true)"

  if [[ "$acp_enabled" != "true" || "$acpx_enabled" != "true" ]]; then
    acp_preflight_status="failed"
    acp_preflight_detail="acp_or_acpx_disabled"
    echo "ACP preflight failed. ACP/acpx must both be enabled." >&2
    echo "Detected: acp.enabled=${acp_enabled:-<unset>} plugins.entries.acpx.enabled=${acpx_enabled:-<unset>}" >&2
    return 1
  fi

  if [[ -n "$allowed_agents_json" ]] && ! echo "$allowed_agents_json" | grep -Eq '"'"$acp_agent"'"'; then
    acp_preflight_status="failed"
    acp_preflight_detail="agent_not_allowed"
    echo "ACP preflight failed. acp.allowedAgents does not include requested agent: $acp_agent" >&2
    echo "Detected acp.allowedAgents: ${allowed_agents_json}" >&2
    return 1
  fi

  if [[ "$permission_mode" != "approve-all" || "$non_interactive" != "deny" ]]; then
    acp_preflight_status="failed"
    acp_preflight_detail="permission_policy_mismatch"
    echo "ACP preflight failed. Required config:" >&2
    echo "  openclaw config set plugins.entries.acpx.config.permissionMode approve-all" >&2
    echo "  openclaw config set plugins.entries.acpx.config.nonInteractivePermissions deny" >&2
    echo "Detected:" >&2
    echo "  permissionMode=${permission_mode:-<unset>}" >&2
    echo "  nonInteractivePermissions=${non_interactive:-<unset>}" >&2
    return 1
  fi

  if ! acp_agent_command_available "$acp_agent"; then
    acp_preflight_status="failed"
    acp_preflight_detail="harness_binary_missing"
    echo "ACP preflight failed. Required harness executable for agent '$acp_agent' was not found on PATH." >&2
    echo "Install/auth the harness or override --acp-agent to an available one." >&2
    return 1
  fi

  acp_preflight_status="ok"
  acp_preflight_detail="ready"
  return 0
}

if [[ "$acp_agent_overridden" != "true" ]]; then
  inferred_owner="$owner_agent"
  if [[ -z "$inferred_owner" ]]; then
    inferred_owner="$coder_codex_agent"
  fi
  acp_agent="$(default_acp_agent_for_owner "$inferred_owner")"
fi

lane_json="$($ROUTER \
  --scope "$scope" \
  --expected-files "$expected_files" \
  --risk "$risk" \
  --acp-available "$acp_available" \
  --tag-heavy "$heavy_tag")"

lane="$(echo "$lane_json" | sed -n 's/.*"lane":"\([^"]*\)".*/\1/p')"
reason="$(echo "$lane_json" | sed -n 's/.*"reason":"\([^"]*\)".*/\1/p')"
fallback_lane="$(echo "$lane_json" | sed -n 's/.*"fallbackLane":"\([^"]*\)".*/\1/p')"

if [[ -z "$lane" || -z "$reason" || -z "$fallback_lane" ]]; then
  echo "Could not parse router output: $lane_json" >&2
  exit 2
fi

requested_lane="$lane"
selected_lane="$lane"

if [[ "$selected_lane" == "acp-external" ]]; then
  if ! acp_preflight_check; then
    if [[ "$fallback_lane" == "codex-subagent" ]]; then
      selected_lane="codex-subagent"
      fallback_applied="true"
      fallback_reason="acp-preflight-failed:${acp_preflight_detail}"
      echo "ACP preflight failed; falling back to ${selected_lane}." >&2
    elif [[ "$execute" == "true" ]]; then
      exit 2
    else
      echo "Dry-run: ACP preflight is not satisfied; execution would fail until fixed." >&2
    fi
  fi
fi

mkdir -p "$RUNS_DIR"
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
meta_path="$RUNS_DIR/${task_id}.${run_stamp}.json"

if [[ -n "$acceptance" ]]; then
  acceptance_line="\n  \"acceptance\": \"${acceptance//\"/\\\"}\","
else
  acceptance_line=""
fi

safe_reason="${reason//\"/\\\"}"
safe_fallback_reason="${fallback_reason//\"/\\\"}"
safe_preflight_status="${acp_preflight_status//\"/\\\"}"
safe_preflight_detail="${acp_preflight_detail//\"/\\\"}"

cat > "$meta_path" <<EOF
{
  "taskId": "${task_id}",
  "timestamp": "${run_stamp}",
  "ownerAgent": "${owner_agent}",
  "coderCodexAgent": "${coder_codex_agent}",
  "orchestratorAgent": "${orchestrator_agent}",
  "repo": "${repo_path}",
  "goal": "${goals//\"/\\\"}",${acceptance_line}
  "routing": {
    "requestedLane": "${requested_lane}",
    "lane": "${selected_lane}",
    "reason": "${safe_reason}",
    "fallbackLane": "${fallback_lane}",
    "fallbackApplied": ${fallback_applied},
    "fallbackReason": "${safe_fallback_reason}",
    "scope": "${scope}",
    "expectedFiles": ${expected_files},
    "risk": "${risk}",
    "heavyTag": ${heavy_tag},
    "acpAvailable": ${acp_available},
    "acpAgent": "${acp_agent}",
    "acpPreflight": {
      "status": "${safe_preflight_status}",
      "detail": "${safe_preflight_detail}"
    }
  }
}
EOF

if [[ "$selected_lane" == "acp-external" ]]; then
  prompt="Use sessions_spawn with runtime=\"acp\", agentId=\"${acp_agent}\", mode=\"run\", cwd=\"${repo_path}\", runTimeoutSeconds=${run_timeout}."
  if [[ -n "$owner_agent" ]]; then
    prompt+=" Assigned owner agent: ${owner_agent}."
  fi
  prompt+=" Task ${task_id}: ${goals}."
  if [[ -n "$acceptance" ]]; then
    prompt+=" Acceptance criteria: ${acceptance}."
  fi
  prompt+=" Return task id, lane, and completion evidence."
  if [[ -z "$OPENCLAW_BIN" || ! -x "$OPENCLAW_BIN" ]]; then
    echo "OpenClaw CLI not found. Set OPENCLAW_BIN or install openclaw on PATH." >&2
    exit 2
  fi
  cmd=("$OPENCLAW_BIN" agent --agent "$orchestrator_agent" --message "$prompt" --timeout "$agent_timeout" --json)
elif [[ "$selected_lane" == "codex-subagent" ]]; then
  codex_target_agent="$coder_codex_agent"
  if [[ -n "$owner_agent" ]]; then
    codex_target_agent="$owner_agent"
  fi
  prompt="Task ${task_id}: ${goals}."
  if [[ -n "$acceptance" ]]; then
    prompt+=" Acceptance criteria: ${acceptance}."
  fi
  prompt+=" Work in repo ${repo_path}."
  prompt+=" Execute this as a native Codex subagent task (spawn_agent)."
  prompt+=" Return JSON only with this exact schema: {\"lane\":\"codex-subagent\",\"taskId\":\"${task_id}\",\"spawnAgentUsed\":true,\"status\":\"succeeded|failed\",\"evidence\":[\"...\"]}."
  if [[ -z "$OPENCLAW_BIN" || ! -x "$OPENCLAW_BIN" ]]; then
    echo "OpenClaw CLI not found. Set OPENCLAW_BIN or install openclaw on PATH." >&2
    exit 2
  fi
  cmd=("$OPENCLAW_BIN" agent --agent "$codex_target_agent" --message "$prompt" --timeout "$agent_timeout" --json)
else
  codex_target_agent="$coder_codex_agent"
  if [[ -n "$owner_agent" ]]; then
    codex_target_agent="$owner_agent"
  fi
  prompt="Task ${task_id}: ${goals}."
  if [[ -n "$acceptance" ]]; then
    prompt+=" Acceptance criteria: ${acceptance}."
  fi
  if [[ -n "$owner_agent" ]]; then
    prompt+=" Assigned owner agent: ${owner_agent}."
  fi
  prompt+=" Work in repo ${repo_path}."
  if [[ -z "$OPENCLAW_BIN" || ! -x "$OPENCLAW_BIN" ]]; then
    echo "OpenClaw CLI not found. Set OPENCLAW_BIN or install openclaw on PATH." >&2
    exit 2
  fi
  cmd=("$OPENCLAW_BIN" agent --agent "$codex_target_agent" --message "$prompt" --timeout "$agent_timeout" --json)
fi

printf 'Routing decision: requested=%s selected=%s (%s), fallback=%s applied=%s\n' "$requested_lane" "$selected_lane" "$reason" "$fallback_lane" "$fallback_applied"
printf 'Metadata written: %s\n' "$meta_path"
printf 'Command: '
printf '%q ' "${cmd[@]}"
printf '\n'

if [[ "$execute" == "true" ]]; then
  set +e
  cmd_output="$("${cmd[@]}" 2>&1)"
  cmd_rc=$?
  set -e

  printf '%s\n' "$cmd_output"

  if [[ "$selected_lane" == "acp-external" ]]; then
    if [[ $cmd_rc -ne 0 ]] || should_trigger_acp_runtime_fallback "$cmd_output"; then
      if [[ "$fallback_lane" == "codex-subagent" ]]; then
        echo "ACP execution appears failed at runtime; retrying via codex-subagent fallback." >&2
        fallback_cmd=(
          "$SCRIPT_DIR/launch-coding-task.sh"
          --task-id "$task_id"
          --repo "$repo_path"
          --goal "$goals"
          --scope "$scope"
          --expected-files "$expected_files"
          --risk "$risk"
          --tag-heavy "$heavy_tag"
          --acp-available false
          --orchestrator-agent "$orchestrator_agent"
          --coder-codex-agent "$coder_codex_agent"
          --agent-timeout "$agent_timeout"
          --execute
        )
        if [[ -n "$owner_agent" ]]; then
          fallback_cmd+=(--owner-agent "$owner_agent")
        fi
        if [[ -n "$acceptance" ]]; then
          fallback_cmd+=(--acceptance "$acceptance")
        fi
        if [[ -n "$acp_agent" ]]; then
          fallback_cmd+=(--acp-agent "$acp_agent")
        fi
        set +e
        fallback_output="$("${fallback_cmd[@]}" 2>&1)"
        fallback_rc=$?
        set -e
        printf '%s\n' "$fallback_output"
        exit $fallback_rc
      fi
    fi
  fi

  if [[ $cmd_rc -ne 0 ]]; then
    exit $cmd_rc
  fi

  if [[ "$selected_lane" == "codex-subagent" ]]; then
    if ! echo "$cmd_output" | grep -Eq '"lane"[[:space:]]*:[[:space:]]*"codex-subagent"' || \
       ! echo "$cmd_output" | grep -Eq '"spawnAgentUsed"[[:space:]]*:[[:space:]]*true'; then
      echo "Codex-subagent contract validation failed: missing lane/spawnAgentUsed markers in response." >&2
      exit 3
    fi
  fi
else
  echo "Dry-run only. Re-run with --execute to launch."
fi
