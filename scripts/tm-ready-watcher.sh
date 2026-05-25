#!/usr/bin/env bash
set -euo pipefail

TM_BASE="${TM_BASE:-http://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="${SCRIPT_DIR}/dwight-launch-from-issue.py"
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

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log() {
  printf '%s [tm-ready-watcher] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" | tee -a "$LOG_FILE"
}

if [[ ! -f "$STATE_FILE" ]]; then
  printf '{\n  "version": 1,\n  "issues": {}\n}\n' > "$STATE_FILE"
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
AUTO_LAUNCH_MARKER = "AUTO_LAUNCH_READY"


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
        state = {"version": 1, "issues": {}}
    state.setdefault("version", 1)
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


def issue_ready(issue):
    assigned_to = normalize_text(issue.get("assigned_to"))
    acceptance = normalize_text(issue.get("acceptance_criteria"))
    branch = normalize_text(issue.get("branch"))
    repo_slug = normalize_text(issue.get("repo_slug"))
    status = normalize_text(issue.get("status"))
    blocked_reason = normalize_text(issue.get("blocked_reason"))
    title = normalize_text(issue.get("title"))
    description = normalize_text(issue.get("description"))

    if assigned_to not in EXECUTING_AGENTS:
        return False, "assigned_to_not_executing_agent"
    if status != "in_progress":
        return False, f"status_{status or 'missing'}"
    if blocked_reason:
        return False, "blocked_reason_present"
    if not branch:
        return False, "branch_missing"
    if not repo_slug:
        return False, "repo_slug_missing"
    if not acceptance:
        return False, "acceptance_missing"
    if not title and not description:
        return False, "goal_missing"
    if AUTO_LAUNCH_MARKER not in description:
        return False, "auto_launch_marker_missing"
    return True, "ready"


def launch_key(issue):
    return "|".join(
        [
            normalize_text(issue.get("status")),
            normalize_text(issue.get("assigned_to")),
            normalize_text(issue.get("branch")),
            normalize_text(issue.get("repo_slug")),
            normalize_text(issue.get("acceptance_criteria")),
            normalize_text(issue.get("updated_at")),
        ]
    )


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

# Reset state for issues that are no longer launch-ready.
for issue in issues:
    issue_id = str(issue.get("id"))
    ready, reason = issue_ready(issue)
    if not ready:
        if issue_id in tracked:
            tracked.pop(issue_id, None)
            results.append(f"reset #{issue_id} ({reason})")

ready_issues = []
for issue in issues:
    ready, reason = issue_ready(issue)
    if not ready:
        results.append(f"skip #{issue.get('id')} ({reason})")
        continue

    key = launch_key(issue)
    issue_id = str(issue["id"])
    existing = tracked.get(issue_id, {})
    if existing.get("launchKey") == key and existing.get("lastMode") == "execute" and existing.get("lastStatus") == "ok":
        results.append(f"skip #{issue_id} (already launched for current ready signature)")
        continue
    ready_issues.append(issue)

ready_issues.sort(key=issue_sort_key, reverse=True)

for issue in ready_issues:
    if launched >= MAX_LAUNCHES_PER_RUN:
        results.append(f"skip #{issue['id']} (launch limit reached)")
        continue

    issue_id = str(issue["id"])
    cmd = [LAUNCHER, "--issue-id", issue_id]
    if EXECUTE_MODE:
        cmd.append("--execute")

    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    success = completed.returncode == 0

    tracked[issue_id] = {
        "launchKey": launch_key(issue) if EXECUTE_MODE and success else "",
        "lastLaunchedAt": now_iso(),
        "lastReturnCode": completed.returncode,
        "lastStatus": "ok" if success else "error",
        "lastMode": "execute" if EXECUTE_MODE else "dry-run",
        "lastOutputSnippet": output[:1000],
    }

    if success:
        launched += 1
        results.append(f"launch #{issue_id} ({'execute' if EXECUTE_MODE else 'dry-run'}) rc=0")
        if EXECUTE_MODE:
            meta_snippet = output[:500].replace("\n", " ")
            comment = (
                "- changed: tm-ready-watcher launched this issue automatically through the canonical Dwight/OpenClaw launcher.\n"
                f"- evidence: mode=execute return_code=0 issue_id={issue_id}\n"
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
