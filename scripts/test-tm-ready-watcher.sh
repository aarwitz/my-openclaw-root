#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER="$SCRIPT_DIR/tm-ready-watcher.sh"

tmpdir="$(mktemp -d)"
cleanup() {
  if [[ -n "${server_pid:-}" ]]; then
    kill "$server_pid" >/dev/null 2>&1 || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -rf "$tmpdir"
}
trap cleanup EXIT

launcher_log="$tmpdir/launcher.log"
comment_log="$tmpdir/comments.log"
issues_dir="$tmpdir/issues"
mkdir -p "$issues_dir"

server_port="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"

cat > "$tmpdir/server.py" <<'PY'
import json
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

root = pathlib.Path(sys.argv[1]).parent
port = int(sys.argv[2])
issues_dir = root / "issues"
comment_log = root / "comments.log"

class Handler(BaseHTTPRequestHandler):
    def _send(self, payload, code=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/issues/"):
            issue_id = self.path.rsplit("/", 1)[-1]
            payload = json.loads((issues_dir / f"{issue_id}.json").read_text())
            return self._send(payload)
        if self.path == "/api/issues":
            payload = [json.loads(path.read_text()) for path in sorted(issues_dir.glob("*.json"))]
            return self._send(payload)
        self._send({"error": "not found"}, code=404)

    def do_POST(self):
        if self.path.startswith("/api/issues/") and self.path.endswith("/comments"):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            with comment_log.open("a", encoding="utf-8") as fh:
                fh.write(body + "\n")
            return self._send({"ok": True})
        self._send({"error": "not found"}, code=404)

    def log_message(self, fmt, *args):
        return

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
PY

cat > "$tmpdir/fake-launcher.py" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

log_path = os.environ["FAKE_LAUNCHER_LOG"]
with open(log_path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:]}) + "\n")

if "--fail" in sys.argv or ("--issue-id" in sys.argv and sys.argv[sys.argv.index("--issue-id") + 1] == "905"):
    raise SystemExit(1)

print("Resolved issue fields:")
print("  issueId=test")
print("Launcher command:")
print("  fake-launcher " + " ".join(sys.argv[1:]))
PY
chmod +x "$tmpdir/fake-launcher.py"

pass_count=0
fail_count=0

pass() {
  echo "PASS: $1"
  pass_count=$((pass_count + 1))
}

fail() {
  echo "FAIL: $1"
  fail_count=$((fail_count + 1))
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  if grep -Fq "$needle" <<<"$haystack"; then
    pass "$label"
  else
    fail "$label (missing '$needle')"
    echo "--- output ---"
    echo "$haystack"
    echo "--------------"
  fi
}

assert_file_line_count() {
  local path="$1"
  local expected="$2"
  local label="$3"
  local actual=0
  if [[ -f "$path" ]]; then
    actual="$(wc -l < "$path")"
  fi
  if [[ "$actual" == "$expected" ]]; then
    pass "$label"
  else
    fail "$label (expected $expected got $actual)"
  fi
}

python3 "$tmpdir/server.py" "$tmpdir/server.py" "$server_port" &
server_pid=$!
sleep 1

write_issue() {
  local issue_id="$1"
  local payload="$2"
  printf '%s\n' "$payload" > "$issues_dir/$issue_id.json"
}

base_issue='{
  "id": 901,
  "title": "Ready coding task",
  "description": "Implement the ready task.",
  "status": "in_progress",
  "assigned_to": "Jerry",
  "branch": "issue-901-ready-task",
  "repo_slug": "aarwitz/openclaw",
  "acceptance_criteria": "- [ ] ship it",
  "auto_launch_enabled": true,
  "launch_state": "ready",
  "launch_error": null,
  "last_launch_at": null,
  "blocked_reason": null,
  "created_at": "2026-05-25T00:00:00",
  "updated_at": "2026-05-25T00:05:00"
}'
write_issue 901 "$base_issue"

run_env=(
  "TM_BASE=http://127.0.0.1:$server_port"
  "TM_READY_WATCHER_LAUNCHER=$tmpdir/fake-launcher.py"
  "OPENCLAW_STATE_DIR=$tmpdir/state"
  "HOME=$tmpdir/home"
  "FAKE_LAUNCHER_LOG=$launcher_log"
)

echo "== TM Ready Watcher Regression =="

output="$(
  env "${run_env[@]}" TM_READY_WATCHER_ALLOW_EXECUTE=true "$WATCHER" --execute --issue-id 901
)"
assert_contains "$output" 'launch #901 (execute) rc=0' "Watcher launches ready issue via canonical launcher"
assert_contains "$output" 'comment #901 posted' "Watcher posts launch comment after execute"
assert_file_line_count "$launcher_log" 2 "Ready issue runs one launcher preflight and one execute launch"

output_repeat="$(
  env "${run_env[@]}" TM_READY_WATCHER_ALLOW_EXECUTE=true "$WATCHER" --execute --issue-id 901
)"
assert_contains "$output_repeat" 'skip #901 (already launched for current ready signature)' "Watcher execute mode is idempotent for same ready signature"
assert_file_line_count "$launcher_log" 2 "Ready issue is not relaunched for identical signature"

queued_issue='{
  "id": 902,
  "title": "Queued coding task",
  "description": "Already queued by Task Manager.",
  "status": "in_progress",
  "assigned_to": "Jerry",
  "branch": "issue-902-queued-task",
  "repo_slug": "aarwitz/openclaw",
  "acceptance_criteria": "- [ ] ship it",
  "auto_launch_enabled": true,
  "launch_state": "queued",
  "launch_error": null,
  "last_launch_at": "2026-05-25T00:06:00",
  "blocked_reason": null,
  "created_at": "2026-05-25T00:00:00",
  "updated_at": "2026-05-25T00:06:00"
}'
write_issue 902 "$queued_issue"
queued_output="$(
  env "${run_env[@]}" "$WATCHER" --issue-id 902
)"
assert_contains "$queued_output" 'adopt #902 (launch_state=queued)' "Watcher adopts queued Task Manager launch without relaunching"
assert_file_line_count "$launcher_log" 2 "Queued issue adoption does not invoke launcher"

aaron_issue='{
  "id": 903,
  "title": "Human owned task",
  "description": "Should stay manual.",
  "status": "in_progress",
  "assigned_to": "Aaron",
  "branch": "issue-903-human-task",
  "repo_slug": "aarwitz/openclaw",
  "acceptance_criteria": "- [ ] decide it",
  "auto_launch_enabled": true,
  "launch_state": "ready",
  "launch_error": null,
  "last_launch_at": null,
  "blocked_reason": null,
  "created_at": "2026-05-25T00:00:00",
  "updated_at": "2026-05-25T00:07:00"
}'
write_issue 903 "$aaron_issue"
aaron_output="$(
  env "${run_env[@]}" TM_READY_WATCHER_ALLOW_EXECUTE=true "$WATCHER" --execute --issue-id 903
)"
assert_contains "$aaron_output" 'skip #903 (assigned_to_not_executing_agent)' "Watcher excludes Aaron-assigned tasks"
assert_file_line_count "$launcher_log" 2 "Aaron-assigned task does not invoke launcher"

approval_issue='{
  "id": 904,
  "title": "Approval gated task",
  "description": "Awaiting Aaron approval before coding.",
  "status": "in_progress",
  "assigned_to": "Jerry",
  "branch": "issue-904-approval-task",
  "repo_slug": "aarwitz/openclaw",
  "acceptance_criteria": "- [ ] wait for sign-off",
  "auto_launch_enabled": true,
  "launch_state": "ready",
  "launch_error": null,
  "last_launch_at": null,
  "blocked_reason": null,
  "created_at": "2026-05-25T00:00:00",
  "updated_at": "2026-05-25T00:08:00"
}'
write_issue 904 "$approval_issue"
approval_output="$(
  env "${run_env[@]}" TM_READY_WATCHER_ALLOW_EXECUTE=true "$WATCHER" --execute --issue-id 904
)"
assert_contains "$approval_output" 'skip #904 (approval_gated)' "Watcher excludes approval-gated tasks"
assert_file_line_count "$launcher_log" 2 "Approval-gated task does not invoke launcher"

bad_repo_issue='{
  "id": 905,
  "title": "Bad repo task",
  "description": "Looks ready but repo cannot be resolved.",
  "status": "in_progress",
  "assigned_to": "Jerry",
  "branch": "issue-905-bad-repo-task",
  "repo_slug": "missing/repo",
  "acceptance_criteria": "- [ ] ship it",
  "auto_launch_enabled": true,
  "launch_state": "ready",
  "launch_error": null,
  "last_launch_at": null,
  "blocked_reason": null,
  "created_at": "2026-05-25T00:00:00",
  "updated_at": "2026-05-25T00:09:00"
}'
write_issue 905 "$bad_repo_issue"
bad_repo_output="$(
  env "${run_env[@]}" TM_READY_WATCHER_ALLOW_EXECUTE=true "$WATCHER" --execute --issue-id 905
)"
assert_contains "$bad_repo_output" 'skip #905 (launcher_preflight_failed)' "Watcher excludes non-executable repo tasks before execute mode"
assert_file_line_count "$launcher_log" 3 "Bad repo task only runs launcher preflight and never executes"

state_check="$(python3 - <<'PY' "$tmpdir/state/tm-ready-watcher-state.json"
import json
import sys
state = json.load(open(sys.argv[1], "r", encoding="utf-8"))
issue = state["issues"]["902"]
print(issue["source"], issue["lastAction"], issue["observedLaunchState"], issue["observedLastLaunchAt"])
PY
)"
assert_contains "$state_check" 'task_manager adopt_queue queued 2026-05-25T00:06:00' "Queued adoption metadata is persisted in watcher state"

echo
echo "Summary: pass=$pass_count fail=$fail_count"

if (( fail_count > 0 )); then
  exit 1
fi
