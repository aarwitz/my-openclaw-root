#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Lane-aware coding task launcher for inline/Codex-subagent architecture.
# Default mode is dry-run so operators can inspect routing before execution.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTER="$SCRIPT_DIR/select-coding-lane.sh"
RUNS_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}/tmp/coding-lane-runs"
source "$SCRIPT_DIR/lib/repo-boundary-policy.sh"

orchestrator_agent="dwight"
coder_codex_agent="jerry"
owner_agent=""
acp_agent="disabled"
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
agent_timeout="300"
execute="false"
requested_lane=""
selected_lane=""
fallback_applied="false"
fallback_reason=""
acp_preflight_status="not-run"
acp_preflight_detail=""
effective_repo_path=""

should_retry_codex_timeout() {
  local output="$1"
  if echo "$output" | grep -Eq 'Request timed out before a response was generated|GatewayTransportError: gateway timeout|codex sdk turn idle timed out|Profile [^ ]+ timed out'; then
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
  --owner-agent <id>      Required assignee id for boundary enforcement.

Routing inputs:
  --scope <low|medium|high>      Default: medium
  --expected-files <int>         Default: 1
  --risk <low|medium|high>       Default: low
  --tag-heavy <true|false>       Default: false
  --acp-available <true|false>   Compatibility flag only; ACP is disabled.

Execution options:
  --acceptance <text>            Optional acceptance criteria.
  --orchestrator-agent <id>      Default: dwight
  --owner-agent <id>             Required assignee (for example: jerry)
  --coder-codex-agent <id>       Default: jerry (Jerry)
  --acp-agent <id>               Compatibility flag only; ignored (ACP disabled).
  --timeout <seconds>            Reserved timeout hint. Default: 1800
  --agent-timeout <seconds>      openclaw agent timeout. Default: 300
  --execute                      Actually invoke OpenClaw command (default is dry-run)
  --help

Policy:
  Boundary checks are fail-closed. Only allowed owner agents may launch coding
  tasks through this wrapper.

Examples:
  launch-coding-task.sh \
    --task-id TM-421 \
    --owner-agent jerry \
    --repo /home/aaron/repos/lidi-task-manager \
    --goal "Fix OAuth callback race" \
    --acceptance "All auth tests green" \
    --scope high --expected-files 12 --risk high --tag-heavy true

  launch-coding-task.sh \
    --task-id TM-422 \
    --owner-agent jerry \
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

if [[ -z "$task_id" || -z "$repo_path" || -z "$goals" || -z "$owner_agent" ]]; then
  echo "Missing required arguments: --task-id, --repo, --goal, --owner-agent" >&2
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

enforce_repo_owner_policy "$owner_agent" "$repo_path"

effective_repo_path="$repo_path"

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

if [[ "$acp_available" == "true" ]]; then
  echo "ACP is disabled by policy; ignoring --acp-available=true and forcing false." >&2
fi
acp_available="false"
acp_preflight_status="not-run"
acp_preflight_detail="acp-disabled-by-policy"

if [[ "$acp_agent" != "disabled" ]]; then
  echo "ACP is disabled by policy; ignoring --acp-agent '$acp_agent'." >&2
fi
acp_agent="disabled"

if [[ ! -f "$ROUTER" ]]; then
  echo "Routing helper missing: $ROUTER" >&2
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

lane_json="$(bash "$ROUTER" \
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

if [[ "$selected_lane" != "inline" && "$selected_lane" != "codex-subagent" ]]; then
  selected_lane="codex-subagent"
  fallback_applied="true"
  fallback_reason="acp-disabled-by-policy"
  echo "Legacy non-Codex lane requested; forcing codex-subagent." >&2
fi

mkdir -p "$RUNS_DIR"
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
meta_path="$RUNS_DIR/${task_id}.${run_stamp}.json"

write_metadata_file() {
  META_TASK_ID="$task_id" \
  META_TIMESTAMP="$run_stamp" \
  META_OWNER_AGENT="$owner_agent" \
  META_CODER_CODEX_AGENT="$coder_codex_agent" \
  META_ORCHESTRATOR_AGENT="$orchestrator_agent" \
  META_REPO="$repo_path" \
  META_GOAL="$goals" \
  META_ACCEPTANCE="$acceptance" \
  META_REQUESTED_LANE="$requested_lane" \
  META_SELECTED_LANE="$selected_lane" \
  META_REASON="$reason" \
  META_FALLBACK_LANE="$fallback_lane" \
  META_FALLBACK_APPLIED="$fallback_applied" \
  META_FALLBACK_REASON="$fallback_reason" \
  META_SCOPE="$scope" \
  META_EXPECTED_FILES="$expected_files" \
  META_RISK="$risk" \
  META_HEAVY_TAG="$heavy_tag" \
  META_ACP_AVAILABLE="$acp_available" \
  META_ACP_AGENT="$acp_agent" \
  META_ACP_PREFLIGHT_STATUS="$acp_preflight_status" \
  META_ACP_PREFLIGHT_DETAIL="$acp_preflight_detail" \
  python3 - "$meta_path" <<'PY'
import json
import os
import sys

path = sys.argv[1]
data = {
    "taskId": os.environ["META_TASK_ID"],
    "timestamp": os.environ["META_TIMESTAMP"],
    "ownerAgent": os.environ["META_OWNER_AGENT"],
    "coderCodexAgent": os.environ["META_CODER_CODEX_AGENT"],
    "orchestratorAgent": os.environ["META_ORCHESTRATOR_AGENT"],
    "repo": os.environ["META_REPO"],
    "goal": os.environ["META_GOAL"],
    "routing": {
        "requestedLane": os.environ["META_REQUESTED_LANE"],
        "lane": os.environ["META_SELECTED_LANE"],
        "reason": os.environ["META_REASON"],
        "fallbackLane": os.environ["META_FALLBACK_LANE"],
        "fallbackApplied": os.environ["META_FALLBACK_APPLIED"] == "true",
        "fallbackReason": os.environ["META_FALLBACK_REASON"],
        "scope": os.environ["META_SCOPE"],
        "expectedFiles": int(os.environ["META_EXPECTED_FILES"]),
        "risk": os.environ["META_RISK"],
        "heavyTag": os.environ["META_HEAVY_TAG"] == "true",
        "acpAvailable": os.environ["META_ACP_AVAILABLE"] == "true",
        "acpAgent": os.environ["META_ACP_AGENT"],
        "acpPreflight": {
            "status": os.environ["META_ACP_PREFLIGHT_STATUS"],
            "detail": os.environ["META_ACP_PREFLIGHT_DETAIL"],
        },
    },
}
if os.environ.get("META_ACCEPTANCE"):
    data["acceptance"] = os.environ["META_ACCEPTANCE"]
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
}

update_metadata_execution() {
  local execution_mode="$1"
  local execution_rc="$2"
  local contract_json="${3:-}"
  META_EXECUTION_MODE="$execution_mode" \
  META_EXECUTION_RC="$execution_rc" \
  META_CONTRACT_JSON="$contract_json" \
  META_OUTPUT="$cmd_output" \
  python3 - "$meta_path" <<'PY'
import json
import os
import sys
from datetime import datetime

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

execution = {
    "mode": os.environ["META_EXECUTION_MODE"],
    "returnCode": int(os.environ["META_EXECUTION_RC"]),
    "completedAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    "outputSnippet": os.environ["META_OUTPUT"][:4000],
}
contract_json = os.environ.get("META_CONTRACT_JSON", "").strip()
if contract_json:
    try:
        execution["contract"] = json.loads(contract_json)
    except json.JSONDecodeError:
        execution["contractRaw"] = contract_json
data["execution"] = execution

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
}

extract_codex_contract_json() {
  CONTRACT_INPUT="${1:-}" python3 - "$task_id" <<'PY'
import json
import os
import re
import sys

task_id = sys.argv[1]
text = os.environ.get("CONTRACT_INPUT", "")

ALLOWED_PR_STATUSES = {"opened", "not-opened", "not-needed", "unknown"}

def validate_obj(obj):
    if not isinstance(obj, dict):
        return None
    if obj.get("lane") != "codex-subagent":
        return None
    if obj.get("taskId") != task_id:
        return None
    if obj.get("spawnAgentUsed") is not True:
        return None
    if obj.get("status") not in {"succeeded", "failed"}:
        return None
    branch = obj.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        return None
    pr = obj.get("pr")
    if not isinstance(pr, dict):
        return None
    pr_status = pr.get("status")
    if pr_status not in ALLOWED_PR_STATUSES:
        return None
    pr_url = pr.get("url")
    if pr_url is not None and not isinstance(pr_url, str):
        return None
    if pr_status == "opened" and not (isinstance(pr_url, str) and pr_url.strip()):
        return None
    if not isinstance(obj.get("evidence"), list):
        return None
    return json.dumps(obj, separators=(",", ":"))

def visit(value):
    if isinstance(value, dict):
        normalized = validate_obj(value)
        if normalized:
            return normalized
        for nested in value.values():
            found = visit(nested)
            if found:
                return found
        return None
    if isinstance(value, list):
        for nested in value:
            found = visit(nested)
            if found:
                return found
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return None
            return visit(parsed)
    return None

for candidate in (text, *text.splitlines()):
    candidate = candidate.strip()
    if not candidate:
        continue
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        continue
    normalized = visit(parsed)
    if normalized:
        print(normalized)
        raise SystemExit(0)

for key in ("finalAssistantVisibleText", "finalAssistantRawText", "text"):
    pattern = re.compile(r'"' + re.escape(key) + r'"\s*:\s*"((?:\\.|[^"])*)"')
    for match in pattern.finditer(text):
        try:
            candidate = json.loads('"' + match.group(1) + '"')
        except json.JSONDecodeError:
            continue
        normalized = visit(candidate)
        if normalized:
            print(normalized)
            raise SystemExit(0)

raise SystemExit(1)
PY
}

extract_contract_status() {
  CONTRACT_JSON_INPUT="${1:-}" python3 - <<'PY'
import json
import os
import sys

try:
    obj = json.loads(os.environ.get("CONTRACT_JSON_INPUT", ""))
except json.JSONDecodeError:
    raise SystemExit(1)
status = obj.get("status")
if not isinstance(status, str):
    raise SystemExit(1)
print(status)
PY
}

write_metadata_file

if [[ "$selected_lane" == "codex-subagent" ]]; then
  codex_target_agent="$coder_codex_agent"
  if [[ -n "$owner_agent" ]]; then
    codex_target_agent="$owner_agent"
  fi
  prompt="Task ${task_id}: ${goals}."
  if [[ -n "$acceptance" ]]; then
    prompt+=" Acceptance criteria: ${acceptance}."
  fi
  prompt+=" Work in repo ${effective_repo_path}."
  prompt+=" Execute this as a native Codex subagent task (spawn_agent)."
  prompt+=" Return JSON only with this exact schema: {\"lane\":\"codex-subagent\",\"taskId\":\"${task_id}\",\"spawnAgentUsed\":true,\"status\":\"succeeded|failed\",\"branch\":\"<branch-name>\",\"pr\":{\"status\":\"opened|not-opened|not-needed|unknown\",\"url\":\"https://...\"|null},\"evidence\":[\"...\"]}."
  prompt+=" If no PR is opened, state why in evidence and use pr.status=\"not-opened\" or \"not-needed\"."
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
  prompt+=" Work in repo ${effective_repo_path}."
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

  if [[ "$selected_lane" == "codex-subagent" && $cmd_rc -ne 0 ]] && should_retry_codex_timeout "$cmd_output"; then
    retry_timeout="$agent_timeout"
    if [[ "$retry_timeout" -lt 300 ]]; then
      retry_timeout=300
    fi
    retry_timeout=$((retry_timeout * 2))
    if [[ "$retry_timeout" -gt 900 ]]; then
      retry_timeout=900
    fi

    retry_cmd=("${cmd[@]}")
    for ((i=0; i<${#retry_cmd[@]}; i++)); do
      if [[ "${retry_cmd[$i]}" == "--timeout" && $((i + 1)) -lt ${#retry_cmd[@]} ]]; then
        retry_cmd[$((i + 1))]="$retry_timeout"
        break
      fi
    done

    echo "Codex subagent timed out; retrying once with --timeout ${retry_timeout}." >&2
    set +e
    retry_output="$("${retry_cmd[@]}" 2>&1)"
    retry_rc=$?
    set -e

    cmd_output="$cmd_output

--- codex-timeout-retry(timeout=${retry_timeout}) ---
$retry_output"
    cmd_rc=$retry_rc
  fi

  printf '%s\n' "$cmd_output"

  if [[ $cmd_rc -ne 0 ]]; then
    update_metadata_execution "execute" "$cmd_rc"
    exit $cmd_rc
  fi

  if [[ "$selected_lane" == "codex-subagent" ]]; then
    contract_json=""
    if ! contract_json="$(extract_codex_contract_json "$cmd_output")"; then
      update_metadata_execution "execute" "$cmd_rc"
      echo "WARNING: codex-subagent contract validation failed inside launch-coding-task.sh; raw output was preserved for higher-level parsing." >&2
      exit "$cmd_rc"
    fi
    update_metadata_execution "execute" "$cmd_rc" "$contract_json"
    printf 'Validated codex-subagent contract: %s\n' "$contract_json"
    contract_status="$(extract_contract_status "$contract_json")"
    if [[ "$contract_status" == "failed" ]]; then
      exit 4
    fi
  else
    update_metadata_execution "execute" "$cmd_rc"
  fi
else
  cmd_output=""
  echo "Dry-run only. Re-run with --execute to launch."
fi
