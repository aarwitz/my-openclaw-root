#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

TM_BASE="${TM_BASE:-https://tm.lidisolutions.ai}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="${TM_READY_WATCHER_LAUNCHER:-${SCRIPT_DIR}/dwight-launch-from-issue.py}"
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw/state}"
STATE_FILE="${STATE_DIR}/tm-ready-watcher-state.json"
LOCK_FILE="${STATE_DIR}/tm-ready-watcher.lock"
LOG_FILE="${HOME}/.openclaw/logs/tm-ready-watcher.log"
MAX_LAUNCHES_PER_RUN="${MAX_LAUNCHES_PER_RUN:-1}"
ALLOW_EXECUTE="${TM_READY_WATCHER_ALLOW_EXECUTE:-false}"

execute="false"
issue_id_filter=""

usage() {
  cat <<'EOF'
Usage:
  tm-ready-watcher.sh [--execute] [--issue-id <id>] [--help]

Behavior:
  - scans Task Manager issues
  - finds code-backed issues that are launch-ready
  - launches at most one new ready issue per run by default
  - records a persistent launch signature to avoid duplicate launches

Options:
  --execute        Actually launch ready issues (requires TM_READY_WATCHER_ALLOW_EXECUTE=true)
  --issue-id <id>  Restrict scan to one issue id
  --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute)
      execute="true"
      shift
      ;;
    --issue-id)
      issue_id_filter="${2:-}"
      shift 2
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

if [[ -n "$issue_id_filter" && ! "$issue_id_filter" =~ ^[0-9]+$ ]]; then
  echo "Invalid --issue-id: $issue_id_filter" >&2
  exit 2
fi

if [[ ! "$MAX_LAUNCHES_PER_RUN" =~ ^[0-9]+$ ]] || (( MAX_LAUNCHES_PER_RUN < 1 )); then
  echo "Invalid MAX_LAUNCHES_PER_RUN: $MAX_LAUNCHES_PER_RUN" >&2
  exit 2
fi

if [[ "$execute" == "true" && "$ALLOW_EXECUTE" != "true" ]]; then
  echo "Refusing execute mode. Set TM_READY_WATCHER_ALLOW_EXECUTE=true to permit live launches." >&2
  exit 2
fi

echo "tm-ready-watcher.sh is disabled: polling-based launch orchestration has been removed in favor of event handlers/hooks/contracts." >&2
echo "Use Task Manager readiness events + GitHub webhook contracts instead." >&2
exit 2

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log() {
  printf '%s [tm-ready-watcher] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" | tee -a "$LOG_FILE"
}

if [[ ! -f "$STATE_FILE" ]]; then
  printf '{\n  "version": 2,\n  "issues": {}\n}\n' > "$STATE_FILE"
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another watcher run is active; skipping"
  exit 0
fi

export TM_BASE
export STATE_FILE
export LAUNCHER
export EXECUTE_MODE="$execute"
export ISSUE_ID_FILTER="$issue_id_filter"
export MAX_LAUNCHES_PER_RUN

python3 - <<'PY'
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from urllib.error import URLError

TM_BASE = os.environ["TM_BASE"].rstrip("/")
STATE_FILE = os.environ["STATE_FILE"]
LAUNCHER = os.environ["LAUNCHER"]
EXECUTE_MODE = os.environ["EXECUTE_MODE"] == "true"
ISSUE_ID_FILTER = os.environ.get("ISSUE_ID_FILTER", "").strip()
MAX_LAUNCHES_PER_RUN = int(os.environ["MAX_LAUNCHES_PER_RUN"])

EXECUTING_AGENTS = {"Jerry", "Resi", "Druck", "Dwight"}
READY_STATES = {"ready", "queued"}
APPROVAL_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(awaiting|pending|requires|need(?:s)?|waiting for)\s+(?:aaron\s+)?approval\b",
        r"\bneeds?\s+(?:human|product|stakeholder)\s+approval\b",
        r"\brequires?\s+sign[ -]?off\b",
        r"\bawaiting\s+(?:human|product|stakeholder)\s+decision\b",
    )
]


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_comment(issue_id: str, content: str):
    url = f"{TM_BASE}/api/issues/{issue_id}/comments"
    body = json.dumps({"content": content, "username": "Jerry"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state():
    with open(STATE_FILE, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    if not isinstance(state, dict):
        state = {"version": 2, "issues": {}}
    state.setdefault("version", 2)
    state.setdefault("issues", {})
    return state


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, STATE_FILE)


def normalize_text(value):
    if not isinstance(value, str):
        return ""
    return value.strip()


def issue_launch_key(issue):
    return "|".join(
        [
            normalize_text(issue.get("status")),
            normalize_text(issue.get("assigned_to")),
            normalize_text(issue.get("branch")),
            normalize_text(issue.get("repo_slug")),
            normalize_text(issue.get("acceptance_criteria")),
            normalize_text(issue.get("title")),
            normalize_text(issue.get("description")),
            "1" if bool(issue.get("auto_launch_enabled")) else "0",
        ]
    )


def issue_ready(issue):
    assigned_to = normalize_text(issue.get("assigned_to"))
    acceptance = normalize_text(issue.get("acceptance_criteria"))
    branch = normalize_text(issue.get("branch"))
    repo_slug = normalize_text(issue.get("repo_slug"))
    status = normalize_text(issue.get("status"))
    launch_state = normalize_text(issue.get("launch_state"))
    blocked_reason = normalize_text(issue.get("blocked_reason"))
    title = normalize_text(issue.get("title"))
    description = normalize_text(issue.get("description"))
    auto_launch_enabled = bool(issue.get("auto_launch_enabled"))
    combined_text = " ".join(
        value
        for value in (
            title,
            description,
            acceptance,
            blocked_reason,
        )
        if value
    )

    if not auto_launch_enabled:
        return False, "auto_launch_disabled"
    if assigned_to not in EXECUTING_AGENTS:
        return False, "assigned_to_not_executing_agent"
    if launch_state not in READY_STATES:
        return False, f"launch_state_{launch_state or 'missing'}"
    if status != "in_progress":
        return False, f"status_{status or 'missing'}"
    if blocked_reason:
        return False, "blocked_reason_present"
    if any(pattern.search(combined_text) for pattern in APPROVAL_PATTERNS):
        return False, "approval_gated"
    if not branch:
        return False, "branch_missing"
    if not repo_slug:
        return False, "repo_slug_missing"
    if not acceptance:
        return False, "acceptance_missing"
    if not title and not description:
        return False, "goal_missing"
    return True, "ready"


def issue_sort_key(issue):
    return normalize_text(issue.get("updated_at")) or normalize_text(issue.get("created_at"))


if ISSUE_ID_FILTER:
    issues = [fetch_json(f"{TM_BASE}/api/issues/{ISSUE_ID_FILTER}")]
else:
    issues = fetch_json(f"{TM_BASE}/api/issues")

state = load_state()
tracked = state["issues"]

launched = 0
results = []

def update_entry(entry, issue, *, action, status, reason, source, mode, return_code=None, output=""):
    entry.update(
        {
            "issueId": str(issue.get("id")),
            "launchKey": issue_launch_key(issue),
            "observedLaunchState": normalize_text(issue.get("launch_state")),
            "observedLastLaunchAt": normalize_text(issue.get("last_launch_at")),
            "observedUpdatedAt": normalize_text(issue.get("updated_at")),
            "lastObservedAt": now_iso(),
            "lastAction": action,
            "lastStatus": status,
            "lastReason": reason,
            "source": source,
            "lastMode": mode,
        }
    )
    if return_code is not None:
        entry["lastReturnCode"] = return_code
    if output:
        entry["lastOutputSnippet"] = output[:1000]


def already_processed(entry, issue, mode):
    if not entry:
        return False
    if entry.get("launchKey") != issue_launch_key(issue):
        return False
    if entry.get("lastMode") != mode:
        return False
    if entry.get("lastStatus") != "ok":
        return False
    issue_state = normalize_text(issue.get("launch_state"))
    if issue_state == "queued":
        return (
            entry.get("source") == "task_manager"
            and entry.get("observedLastLaunchAt") == normalize_text(issue.get("last_launch_at"))
        )
    return entry.get("source") == "watcher"


def run_launcher(issue_id, execute):
    cmd = [LAUNCHER, "--issue-id", issue_id]
    if execute:
        cmd.append("--execute")
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed, output

ready_issues = []
for issue in issues:
    ready, reason = issue_ready(issue)
    if not ready:
        entry = tracked.setdefault(str(issue.get("id")), {})
        update_entry(
            entry,
            issue,
            action="skip",
            status="skipped",
            reason=reason,
            source="watcher",
            mode="execute" if EXECUTE_MODE else "dry-run",
        )
        results.append(f"skip #{issue.get('id')} ({reason})")
        continue

    issue_id = str(issue["id"])
    existing = tracked.get(issue_id, {})
    mode_key = "execute" if EXECUTE_MODE else "dry-run"
    if already_processed(existing, issue, mode_key):
        results.append(f"skip #{issue_id} (already launched for current ready signature)")
        continue
    ready_issues.append(issue)

ready_issues.sort(key=issue_sort_key, reverse=True)

for issue in ready_issues:
    if launched >= MAX_LAUNCHES_PER_RUN:
        entry = tracked.setdefault(str(issue["id"]), {})
        update_entry(
            entry,
            issue,
            action="skip",
            status="skipped",
            reason="launch_limit_reached",
            source="watcher",
            mode="execute" if EXECUTE_MODE else "dry-run",
        )
        results.append(f"skip #{issue['id']} (launch limit reached)")
        continue

    issue_id = str(issue["id"])
    entry = tracked.setdefault(issue_id, {})
    if normalize_text(issue.get("launch_state")) == "queued":
        update_entry(
            entry,
            issue,
            action="adopt_queue",
            status="ok",
            reason="task_manager_already_queued",
            source="task_manager",
            mode="observe",
        )
        results.append(f"adopt #{issue_id} (launch_state=queued)")
        continue

    preflight, preflight_output = run_launcher(issue_id, False)
    if preflight.returncode != 0:
        update_entry(
            entry,
            issue,
            action="skip",
            status="skipped",
            reason="launcher_preflight_failed",
            source="watcher",
            mode="execute" if EXECUTE_MODE else "dry-run",
            return_code=preflight.returncode,
            output=preflight_output,
        )
        results.append(f"skip #{issue_id} (launcher_preflight_failed)")
        continue

    if not EXECUTE_MODE:
        update_entry(
            entry,
            issue,
            action="launch",
            status="ok",
            reason="ready",
            source="watcher",
            mode="dry-run",
            return_code=preflight.returncode,
            output=preflight_output,
        )
        results.append(f"launch #{issue_id} (dry-run) rc=0")
        continue

    completed, output = run_launcher(issue_id, True)
    success = completed.returncode == 0
    update_entry(
        entry,
        issue,
        action="launch",
        status="ok" if success else "error",
        reason="ready",
        source="watcher",
        mode="execute",
        return_code=completed.returncode,
        output=output,
    )

    if success:
        if EXECUTE_MODE:
            launched += 1
        results.append(f"launch #{issue_id} ({'execute' if EXECUTE_MODE else 'dry-run'}) rc=0")
        if EXECUTE_MODE:
            meta_snippet = output[:500].replace("\n", " ")
            comment = (
                "- changed: tm-ready-watcher launched this ready coding issue through the canonical Dwight/OpenClaw launcher.\n"
                f"- evidence: mode=execute return_code=0 issue_id={issue_id} launch_state=ready\n"
                f"- next step: continue implementation on the existing issue/branch/PR thread. launcher output snippet: {meta_snippet}"
            )
            try:
                post_comment(issue_id, comment)
                results.append(f"comment #{issue_id} posted")
            except URLError as exc:
                results.append(f"comment #{issue_id} failed ({exc})")
    else:
        results.append(f"launch #{issue_id} failed rc={completed.returncode}")

save_state(state)

print(json.dumps({
    "timestamp": now_iso(),
    "mode": "execute" if EXECUTE_MODE else "dry-run",
    "launched": launched,
    "results": results,
    "stateFile": STATE_FILE,
}, indent=2))

if any(r.startswith("launch ") and "failed" in r for r in results):
    sys.exit(1)
PY
