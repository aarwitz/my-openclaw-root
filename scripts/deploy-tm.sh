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
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || {
  echo "FATAL: dirty tree — commit first (deploys must be attributable to a commit)" >&2; exit 2; }
git fetch origin --quiet
[[ "$(git rev-parse main)" == "$(git rev-parse origin/main)" ]] || {
  echo "FATAL: main diverged from origin/main — sync first" >&2; exit 2; }

GIT_SHA="$(git rev-parse HEAD)"
BUILT_AT="$(date -u +%FT%TZ)"
WR="./node_modules/.bin/wrangler"; [[ -x "$WR" ]] || WR="npx wrangler"

echo "[deploy-tm] deploying $GIT_SHA from main"
$WR deploy --var "GIT_SHA:$GIT_SHA" --var "BUILT_AT:$BUILT_AT" 2>&1 | tail -5

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
  echo "FATAL: live /api/version reports '$LIVE_SHA', expected $GIT_SHA — investigate, consider: $WR rollback" >&2
  exit 1
fi