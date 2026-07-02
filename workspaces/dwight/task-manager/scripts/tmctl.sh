#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
BASE_URL="${TM_BASE_URL:-https://tm.lidisolutions.ai}"
COMPOSE_FILE="${TM_COMPOSE_FILE:-$ROOT_DIR/docker-compose.yml}"
COMPOSE_PROJECT="${TM_COMPOSE_PROJECT:-lidi-task-manager-local}"
CONTAINER_NAME="${TM_CONTAINER_NAME:-lidi-task-manager-local}"
TM_DB_PATH="${TM_DB_PATH:-/home/aaron/.openclaw/workspaces/dwight/taskmanager.db}"
TM_PUBLISH_HOST="${TM_PUBLISH_HOST:-0.0.0.0}"
TM_PORT="${TM_PORT:-8787}"
REPO_LOCAL_DB_PATH="$ROOT_DIR/taskmanager.db"
CANONICAL_TM_BASE_URL="https://tm.lidisolutions.ai"

usage() {
  cat <<'EOF'
Usage: scripts/tmctl.sh <command> [args]

Commands:
  status                         Show runtime, listener, and health status
  start                          Start Task Manager container
  stop                           Stop Task Manager container
  restart                        Restart Task Manager container
  logs [n]                       Tail service logs (default 120 lines)
  dev                            Start compose stack for development
  verify                         Run functional verification checks
  stats                          Print DB counts and key integrity checks
  routes                         List API routes declared in backend/main.py
  login <username>               Call /api/users/login and print user payload
  api <METHOD> <PATH> [JSON]     Send raw API request (example: api GET /api/issues)
  help                           Show this help

Environment:
  TM_BASE_URL                    API base URL (default: https://tm.lidisolutions.ai)
  TM_COMPOSE_FILE                Compose file path
  TM_COMPOSE_PROJECT             Compose project name
  TM_CONTAINER_NAME              Task Manager container name
  TM_DB_PATH                     Canonical Task Manager SQLite file
  TM_PUBLISH_HOST                Host bind for published TM port (default: 0.0.0.0)
  TM_PORT                        Port for local TM runtime (default: 8787)
EOF
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -p "$COMPOSE_PROJECT" -f "$COMPOSE_FILE" "$@"
    return
  fi
  echo "No docker compose plugin available; using direct docker fallback."
  return 1
}

ensure_hosted_base_url() {
  if [[ "$BASE_URL" != "$CANONICAL_TM_BASE_URL" ]]; then
    echo "ERROR: TM_BASE_URL must be $CANONICAL_TM_BASE_URL; got $BASE_URL"
    echo "Local or alternate Task Manager endpoints are not allowed."
    return 2
  fi
}

have_compose_plugin() {
  docker compose version >/dev/null 2>&1
}

container_exists() {
  docker ps -a --format '{{.Names}}' | grep -x "$CONTAINER_NAME" >/dev/null 2>&1
}

container_running() {
  docker ps --format '{{.Names}}' | grep -x "$CONTAINER_NAME" >/dev/null 2>&1
}

docker_direct_build() {
  docker build -f "$ROOT_DIR/Dockerfile.tm" -t lidi-task-manager-local:local "$ROOT_DIR"
}

docker_direct_up() {
  docker_direct_build
  if container_exists; then
    docker rm -f "$CONTAINER_NAME" >/dev/null
  fi
  docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -u 1000:1000 \
    -w /workspace/backend \
    -e PYTHONUNBUFFERED=1 \
    -e TASKMANAGER_DB_PATH=/workspace/taskmanager.db \
    -v "$ROOT_DIR":/workspace:rw \
    -v "$TM_DB_PATH":/workspace/taskmanager.db:rw \
    -p "$TM_PUBLISH_HOST":"$TM_PORT":"$TM_PORT" \
    lidi-task-manager-local:local \
    python -m uvicorn main:app --host 0.0.0.0 --port "$TM_PORT" >/dev/null
}

docker_direct_stop() {
  if container_exists; then
    docker stop "$CONTAINER_NAME" >/dev/null
  fi
}

docker_direct_restart() {
  if container_exists; then
    docker restart "$CONTAINER_NAME" >/dev/null
  else
    docker_direct_up
  fi
}

docker_direct_logs() {
  local lines="${1:-120}"
  docker logs --tail "$lines" "$CONTAINER_NAME"
}

api_get() {
  local path="${1:-/}"
  if curl -fsS "$BASE_URL$path" >/dev/null 2>&1; then
    curl -fsS "$BASE_URL$path"
    return
  fi
  if container_running; then
    docker exec -i -e TM_BASE_URL="$BASE_URL" "$CONTAINER_NAME" python - <<PY
import os
import urllib.request
path = "$path"
url = os.environ["TM_BASE_URL"] + path
print(urllib.request.urlopen(url, timeout=20).read().decode("utf-8"))
PY
    return
  fi
  return 1
}

api_request() {
  local method="$1"
  local path="$2"
  local body="${3:-}"

  if [[ -n "$body" ]]; then
    if curl -fsS "$BASE_URL$path" -X "$method" -H 'Content-Type: application/json' -d "$body" >/dev/null 2>&1; then
      curl -fsS "$BASE_URL$path" -X "$method" -H 'Content-Type: application/json' -d "$body"
      return
    fi
  else
    if curl -fsS "$BASE_URL$path" -X "$method" >/dev/null 2>&1; then
      curl -fsS "$BASE_URL$path" -X "$method"
      return
    fi
  fi

  if container_running; then
    docker exec -i \
      -e TM_BASE_URL="$BASE_URL" \
      -e TM_METHOD="$method" \
      -e TM_PATH="$path" \
      -e TM_BODY="$body" \
      "$CONTAINER_NAME" python - <<'PY'
import json
import os
import urllib.request

method = os.environ.get("TM_METHOD", "GET")
path = os.environ.get("TM_PATH", "/")
body = os.environ.get("TM_BODY", "")
url = os.environ["TM_BASE_URL"] + path
headers = {"Accept": "application/json"}
data = None
if body:
    headers["Content-Type"] = "application/json"
    data = body.encode("utf-8")
req = urllib.request.Request(url, method=method, data=data, headers=headers)
with urllib.request.urlopen(req, timeout=20) as resp:
    print(resp.read().decode("utf-8"))
PY
    return
  fi

  return 1
}

ensure_compose_file() {
  if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "Compose file not found: $COMPOSE_FILE"
    return 1
  fi
}

runtime_mode() {
  if container_running; then
    echo "docker-running"
  else
    echo "docker-stopped"
  fi
}

http_ok() {
  if curl -fsS "$BASE_URL/" >/dev/null 2>&1; then
    return 0
  fi
  if container_running; then
    docker exec -i -e TM_BASE_URL="$BASE_URL" "$CONTAINER_NAME" python - <<'PY' >/dev/null
import os
import urllib.request
urllib.request.urlopen(os.environ["TM_BASE_URL"] + '/', timeout=10).read()
PY
    return
  fi
  return 1
}

wait_for_health() {
  local attempts="${1:-20}"
  local i
  for i in $(seq 1 "$attempts"); do
    if http_ok; then
      return 0
    fi
    :
  done
  return 1
}

listener_grep() {
  if command -v rg >/dev/null 2>&1; then
    rg ":${TM_PORT}\\b"
  else
    grep -E ":${TM_PORT}([^0-9]|$)"
  fi
}

warn_repo_local_db_conflict() {
  if [[ -f "$REPO_LOCAL_DB_PATH" ]]; then
    echo "warning=repo_local_db_present path=$REPO_LOCAL_DB_PATH"
    echo "warning_detail=canonical runtime DB is $TM_DB_PATH; repo-local taskmanager.db is not the live container mount"
  fi
}

cmd_status() {
  local mode
  mode="$(runtime_mode)"
  echo "mode=$mode"
  echo "base_url=$BASE_URL"
  echo "compose_file=$COMPOSE_FILE"
  echo "container_name=$CONTAINER_NAME"
  echo "db_path=$TM_DB_PATH"
  warn_repo_local_db_conflict

  if have_compose_plugin && [[ -f "$COMPOSE_FILE" ]]; then
    compose ps || true
  else
    docker ps -a --filter "name=^/${CONTAINER_NAME}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
  fi

  echo "--- listener ---"
  ss -ltnp | listener_grep || true

  echo "--- health ---"
  if wait_for_health 2; then
    echo "GET /: OK"
  else
    echo "GET /: FAILED"
    return 1
  fi
}

cmd_start() {
  if have_compose_plugin && [[ -f "$COMPOSE_FILE" ]]; then
    compose up -d --build taskmanager
  else
    docker_direct_up
  fi
  wait_for_health 60 || true
  cmd_status
}

cmd_stop() {
  if have_compose_plugin && [[ -f "$COMPOSE_FILE" ]]; then
    compose stop taskmanager
  else
    docker_direct_stop
  fi
}

cmd_restart() {
  if have_compose_plugin && [[ -f "$COMPOSE_FILE" ]]; then
    compose restart taskmanager
  else
    docker_direct_restart
  fi
  wait_for_health 60 || true
  cmd_status
}

cmd_logs() {
  local lines="${1:-120}"
  if have_compose_plugin && [[ -f "$COMPOSE_FILE" ]]; then
    compose logs --tail "$lines" taskmanager
  else
    docker_direct_logs "$lines"
  fi
}

cmd_dev() {
  if have_compose_plugin && [[ -f "$COMPOSE_FILE" ]]; then
    compose up taskmanager
  else
    docker_direct_up
    docker_direct_logs 200
  fi
}

cmd_verify() {
  warn_repo_local_db_conflict
  if command -v python3 >/dev/null 2>&1 && curl -fsS "$BASE_URL/" >/dev/null 2>&1; then
    python3 "$ROOT_DIR/scripts/tm_verify.py" --base-url "$BASE_URL" --db "$TM_DB_PATH"
    return
  fi

  if ! command -v jq >/dev/null 2>&1; then
    echo "verify requires python3 or jq"
    return 1
  fi

  local root_ok=0
  if http_ok; then
    root_ok=1
  fi
  local issues_json sprints_json issues_count sprints_count comments_count sprint5
  issues_json="$(api_get "/api/issues")"
  sprints_json="$(api_get "/api/sprints")"
  issues_count="$(echo "$issues_json" | jq 'length')"
  sprints_count="$(echo "$sprints_json" | jq 'length')"
  comments_count="$(echo "$issues_json" | jq '[.[].comments | length] | add // 0')"
  sprint5="$(echo "$sprints_json" | jq -r '.[] | select(.id==5) | .name' | head -n1)"
  local missing
  missing="$(echo "$issues_json" | jq -r '[.[].id] as $ids | [120,121,122,123,124,125] | map(select(. as $i | ($ids | index($i) | not))) | @json')"

  echo "base_url=$BASE_URL"
  echo "db_path=$TM_DB_PATH"
  echo "issues=$issues_count"
  echo "sprints=$sprints_count"
  echo "comments=$comments_count"
  echo "sprint_5=$sprint5"

  local failed=0
  if [[ "$root_ok" != "1" ]]; then
    echo "verification=FAILED"
    echo "- GET / failed"
    return 1
  fi
  if [[ "$missing" != "[]" ]]; then failed=1; echo "- missing issue IDs in 120-125: $missing"; fi

  if [[ "$failed" == "1" ]]; then
    echo "verification=FAILED"
    return 1
  fi
  echo "verification=OK"
}

cmd_stats() {
  if [[ ! -f "$TM_DB_PATH" ]]; then
    echo "db_path=$TM_DB_PATH"
    echo "DB file missing"
    return 1
  fi
  warn_repo_local_db_conflict

  if ! command -v python3 >/dev/null 2>&1; then
    if ! command -v jq >/dev/null 2>&1; then
      echo "stats requires python3 or jq"
      return 1
    fi
    local issues_json sprints_json issues_count sprints_count comments_count sprint5 ids_present
    issues_json="$(api_get "/api/issues")"
    sprints_json="$(api_get "/api/sprints")"
    issues_count="$(echo "$issues_json" | jq 'length')"
    sprints_count="$(echo "$sprints_json" | jq 'length')"
    comments_count="$(echo "$issues_json" | jq '[.[].comments | length] | add // 0')"
    sprint5="$(echo "$sprints_json" | jq -r '.[] | select(.id==5) | .name' | head -n1)"
    ids_present="$(echo "$issues_json" | jq '[.[].id] as $ids | [120,121,122,123,124,125] | all(. as $i | ($ids | index($i)))')"
    echo "db_path=$TM_DB_PATH"
    echo "issues=$issues_count"
    echo "sprints=$sprints_count"
    echo "comments=$comments_count"
    echo "sprint_5=$sprint5"
    echo "issues_120_125_present=$ids_present"
    return
  fi

  DB_PATH="$(realpath "$TM_DB_PATH")"
  export DB_PATH
  python3 - <<'PY'
import os
import sqlite3
from pathlib import Path

db = Path(os.environ["DB_PATH"]).resolve()
conn = sqlite3.connect(str(db))
cur = conn.cursor()
issues = cur.execute("select count(*) from issues").fetchone()[0]
sprints = cur.execute("select count(*) from sprints").fetchone()[0]
comments = cur.execute("select count(*) from comments").fetchone()[0]
s5 = cur.execute("select name from sprints where id=5").fetchone()
ids = {row[0] for row in cur.execute("select id from issues where id between 120 and 125")}
conn.close()
print(f"db_path={db}")
print(f"issues={issues}")
print(f"sprints={sprints}")
print(f"comments={comments}")
print(f"sprint_5={s5[0] if s5 else None}")
print(f"issues_120_125_present={all(i in ids for i in range(120,126))}")
PY
}

cmd_routes() {
  rg -n "@app\\.(get|post|put|delete)\\(" "$BACKEND_DIR/main.py"
}

cmd_login() {
  local username="${1:-}"
  if [[ -z "$username" ]]; then
    echo "usage: scripts/tmctl.sh login <username>"
    return 1
  fi
  api_request "POST" "/api/users/login" "{\"username\":\"$username\"}" | python3 -m json.tool
}

cmd_api() {
  local method="${1:-}"
  local path="${2:-}"
  local body="${3:-}"
  if [[ -z "$method" || -z "$path" ]]; then
    echo "usage: scripts/tmctl.sh api <METHOD> <PATH> [JSON_BODY]"
    return 1
  fi
  if [[ -n "$body" ]]; then
    api_request "$method" "$path" "$body" | python3 -m json.tool
  else
    api_request "$method" "$path" | python3 -m json.tool
  fi
}

main() {
  local cmd="${1:-help}"
  shift || true

  ensure_hosted_base_url || return $?

  case "$cmd" in
    status) cmd_status "$@" ;;
    start) cmd_start "$@" ;;
    stop) cmd_stop "$@" ;;
    restart) cmd_restart "$@" ;;
    logs) cmd_logs "$@" ;;
    dev) cmd_dev "$@" ;;
    verify) cmd_verify "$@" ;;
    stats) cmd_stats "$@" ;;
    routes) cmd_routes "$@" ;;
    login) cmd_login "$@" ;;
    api) cmd_api "$@" ;;
    help|-h|--help) usage ;;
    *)
      echo "unknown command: $cmd"
      usage
      return 1
      ;;
  esac
}

main "$@"
