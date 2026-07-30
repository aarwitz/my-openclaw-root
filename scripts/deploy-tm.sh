#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# deploy-tm.sh — the ONLY sanctioned way to deploy the tm.lidisolutions.ai worker.
#
# Born 2026-07-29 after the deploy-provenance fiasco: an untracked 07-28 deploy
# changed validation behavior, nobody could say what commit was live, and the
# operator session concluded "the live code isn't in this repo at all" (it was).
# This script makes every deploy attributable and verifiable:
#   1. refuses to deploy from a dirty tree or any branch but main synced with origin;
#   2. injects GIT_SHA/BUILT_AT vars (served at /api/version);
#   3. verifies the live endpoint reports the sha it just shipped — fails loudly if not.
# Secrets (e.g. AGENT_TM_USERS, set 2026-07-29) are untouched by var injection.

TM_REPO="${TM_REPO:-$HOME/repos/lidi-task-manager}"
cd "$TM_REPO" || { echo "FATAL: repo missing at $TM_REPO" >&2; exit 2; }

BRANCH="$(git branch --show-current)"
[[ "$BRANCH" == "main" ]] || { echo "FATAL: on '$BRANCH', deploys only from main" >&2; exit 2; }
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || {
  echo "FATAL: dirty tree — commit first (deploys must be attributable to a commit)" >&2; exit 2; }
[[ "$(git worktree list --porcelain | awk '/^worktree / { count += 1 } END { print count + 0 }')" == "1" ]] || {
  echo "FATAL: extra worktrees exist — remove them before deployment" >&2; exit 2; }
git fetch origin --quiet --prune
[[ "$(git rev-parse main)" == "$(git rev-parse origin/main)" ]] || {
  echo "FATAL: main diverged from origin/main — sync first" >&2; exit 2; }

EXTRA_LOCAL_BRANCHES="$(git for-each-ref --format='%(refname:short)' refs/heads | grep -vx 'main' || true)"
[[ -z "$EXTRA_LOCAL_BRANCHES" ]] || {
  echo "FATAL: extra local branches exist: ${EXTRA_LOCAL_BRANCHES//$'\n'/, }" >&2; exit 2; }
EXTRA_REMOTE_BRANCHES="$(
  git for-each-ref --format='%(refname:short)' refs/remotes/origin |
    grep -Ev '^origin/(HEAD|main)$' || true
)"
[[ -z "$EXTRA_REMOTE_BRANCHES" ]] || {
  echo "FATAL: extra origin branches exist: ${EXTRA_REMOTE_BRANCHES//$'\n'/, }" >&2; exit 2; }

echo "[deploy-tm] running repository test contract"
npm test
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || {
  echo "FATAL: tests changed the working tree" >&2; exit 2; }

GIT_SHA="$(git rev-parse HEAD)"
BUILT_AT="$(date -u +%FT%TZ)"
if [[ -x "./node_modules/.bin/wrangler" ]]; then
  WRANGLER=(./node_modules/.bin/wrangler)
else
  WRANGLER=(npx wrangler)
fi

LIVE_SHA="$(curl -sS --max-time 15 https://tm.lidisolutions.ai/api/version |
  python3 -c "import json,sys; print(json.load(sys.stdin).get('sha',''))" 2>/dev/null || true)"
if [[ "$LIVE_SHA" == "$GIT_SHA" ]]; then
  echo "[deploy-tm] commit $GIT_SHA is already live; skipping duplicate deployment"
  exit 0
fi

echo "[deploy-tm] deploying $GIT_SHA from main"
"${WRANGLER[@]}" deploy --var "GIT_SHA:$GIT_SHA" --var "BUILT_AT:$BUILT_AT" 2>&1 | tail -5

echo "[deploy-tm] verifying live /api/version (propagation can take ~30s)"
LIVE_SHA=""
for i in 1 2 3 4 5 6; do
  sleep 5
  LIVE_SHA="$(curl -s --max-time 15 https://tm.lidisolutions.ai/api/version | python3 -c "import json,sys; print(json.load(sys.stdin).get('sha',''))" 2>/dev/null || true)"
  [[ "$LIVE_SHA" == "$GIT_SHA" ]] && break
done
if [[ "$LIVE_SHA" == "$GIT_SHA" ]]; then
  echo "[deploy-tm] OK — live sha matches deployed commit $GIT_SHA"
else
  echo "FATAL: live /api/version reports '$LIVE_SHA', expected $GIT_SHA — investigate with Wrangler deployments" >&2
  exit 1
fi
