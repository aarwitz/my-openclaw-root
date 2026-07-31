#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# Canonical production release path for lidisolutions.ai.
# The custom domain is a Cloudflare Pages project. Never use `wrangler deploy`
# here: that targets the obsolete standalone Worker service with the same name.

LIDI_REPO="${LIDI_SOLUTIONS_REPO:-/home/aaron/repos/lidi-solutions}"
WRANGLER_OAUTH="/home/aaron/.config/.wrangler/config/default.toml"
CRED_DIR="/home/aaron/.openclaw/credentials/cloudflare"
TOKEN_FILE="${CRED_DIR}/account-token"
META_FILE="${CRED_DIR}/account-meta.json"
PRODUCTION_ORIGIN="${LIDI_SOLUTIONS_ORIGIN:-https://lidisolutions.ai}"
DEPLOY_TMP=""

cleanup() {
  if [[ -n "$DEPLOY_TMP" && -d "$DEPLOY_TMP" ]]; then
    rm -rf -- "$DEPLOY_TMP"
  fi
}
trap cleanup EXIT

fatal() {
  echo "FATAL: $*" >&2
  exit 1
}

if [[ ! -d "$LIDI_REPO" ]]; then
  echo "FATAL: lidi-solutions repository missing at $LIDI_REPO" >&2
  exit 2
fi

cd "$LIDI_REPO"

[[ "$(git branch --show-current)" == "main" ]] ||
  fatal "deploys are allowed only from main"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fatal "working tree is dirty or has untracked files"
[[ "$(git worktree list --porcelain | awk '/^worktree / { count += 1 } END { print count + 0 }')" == "1" ]] ||
  fatal "extra worktrees exist; remove them before deployment"

git fetch --quiet --prune origin main
[[ "$(git rev-parse HEAD)" == "$(git rev-parse refs/remotes/origin/main)" ]] ||
  fatal "main is not exactly synchronized with origin/main"

EXTRA_LOCAL_BRANCHES="$(git for-each-ref --format='%(refname:short)' refs/heads | grep -vx 'main' || true)"
[[ -z "$EXTRA_LOCAL_BRANCHES" ]] ||
  fatal "extra local branches exist: ${EXTRA_LOCAL_BRANCHES//$'\n'/, }"

EXTRA_REMOTE_BRANCHES="$(
  git for-each-ref --format='%(refname:short)' refs/remotes/origin |
    grep -Ev '^origin/(HEAD|main)$' || true
)"
[[ -z "$EXTRA_REMOTE_BRANCHES" ]] ||
  fatal "extra origin branches exist: ${EXTRA_REMOTE_BRANCHES//$'\n'/, }"

if [[ ! -x "$LIDI_REPO/node_modules/.bin/wrangler" ]]; then
  echo "[deploy-site] installing locked dependencies"
  npm ci --no-audit --no-fund
fi

echo "[deploy-site] running repository test contract"
npm test
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fatal "tests changed the working tree"

GIT_SHA="$(git rev-parse HEAD)"
printf '{"sha":"%s","built_at":"%s","repo":"lidi-solutions"}\n' \
  "$GIT_SHA" "$(date -u +%FT%TZ)" > dist/version.json

read_live_sha() {
  curl --fail --silent --show-error --max-time 15 \
    "$PRODUCTION_ORIGIN/version.json" |
    python3 -c "import json,sys; print(json.load(sys.stdin).get('sha',''))" 2>/dev/null
}

verify_production() {
  local live_sha=""
  local route=""
  local status=""

  for _attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    live_sha="$(read_live_sha || true)"
    [[ "$live_sha" == "$GIT_SHA" ]] && break
    sleep 5
  done
  [[ "$live_sha" == "$GIT_SHA" ]] ||
    fatal "live /version.json reports '$live_sha', expected '$GIT_SHA'"

  for route in / /solutions /solutions/tapp; do
    status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
      --max-time 15 "$PRODUCTION_ORIGIN$route")"
    [[ "$status" == "200" ]] || fatal "$route returned HTTP $status"
  done

  local trader_redirect=""
  trader_redirect="$(curl --silent --show-error --output /dev/null \
    --write-out '%{http_code} %{redirect_url}' --max-time 15 \
    "$PRODUCTION_ORIGIN/solutions/trader_intel/app/")"
  [[ "$trader_redirect" == "302 $PRODUCTION_ORIGIN/solutions/trader_intel/app/login" ]] ||
    fatal "protected Trader Intel route returned '$trader_redirect'"

  status="$(curl --silent --show-error --output "$DEPLOY_TMP/trader-live.json" \
    --write-out '%{http_code}' --max-time 15 "$PRODUCTION_ORIGIN/api/trader-live")"
  if [[ "$status" == "200" ]]; then
    node "$LIDI_REPO/scripts/audit-trader-live.mjs"
  elif [[ "$status" == "503" ]]; then
    python3 -c \
      "import json,sys; p=json.load(open(sys.argv[1])); assert isinstance(p,dict) and p.get('error')" \
      "$DEPLOY_TMP/trader-live.json" ||
      fatal "/api/trader-live returned a malformed fail-closed response"
    echo "[deploy-site] Trader Intel live snapshot is unavailable and correctly fails closed"
  else
    fatal "/api/trader-live returned unexpected HTTP $status"
  fi
}

prune_noncanonical_pages_deployments() {
  [[ -f "$TOKEN_FILE" && -f "$META_FILE" ]] ||
    fatal "Cloudflare API credentials are required to enforce canonical-only deployment inventory"

  local api_token=""
  local account_id=""
  local api_base=""
  local project_json=""
  local deployments_json=""
  local canonical_id=""
  local deployment_id=""
  local delete_json=""
  local total=""
  local -a stale_ids=()

  api_token="$(tr -d '\r\n' < "$TOKEN_FILE")"
  account_id="$(python3 -c "import json; print(json.load(open('$META_FILE'))['account_id'])")"
  [[ -n "$api_token" && -n "$account_id" ]] || fatal "Cloudflare API credentials are incomplete"
  api_base="https://api.cloudflare.com/client/v4/accounts/${account_id}/pages/projects/lidi-solutions"

  project_json="$(curl --fail --silent --show-error --max-time 20 \
    "$api_base" -H "Authorization: Bearer ${api_token}")" ||
    fatal "could not resolve the canonical Pages deployment"
  canonical_id="$(jq -r '.result.canonical_deployment.id // empty' <<<"$project_json")"
  [[ -n "$canonical_id" ]] || fatal "Pages project has no canonical deployment"

  while true; do
    deployments_json="$(curl --fail --silent --show-error --max-time 20 \
      "${api_base}/deployments?page=1&per_page=25" \
      -H "Authorization: Bearer ${api_token}")" ||
      fatal "could not list Pages deployments"
    mapfile -t stale_ids < <(
      jq -r --arg canonical "$canonical_id" \
        '.result[] | select(.id != $canonical) | .id' <<<"$deployments_json"
    )
    [[ "${#stale_ids[@]}" -gt 0 ]] || break

    for deployment_id in "${stale_ids[@]}"; do
      [[ "$deployment_id" != "$canonical_id" ]] || fatal "refusing to delete canonical deployment"
      delete_json="$(curl --fail --silent --show-error --max-time 20 -X DELETE \
        "${api_base}/deployments/${deployment_id}" \
        -H "Authorization: Bearer ${api_token}")" ||
        fatal "failed to delete noncanonical Pages deployment ${deployment_id}"
      [[ "$(jq -r '.success // false' <<<"$delete_json")" == "true" ]] ||
        fatal "Cloudflare rejected deletion of noncanonical deployment ${deployment_id}"
      echo "[deploy-site] removed noncanonical Pages deployment ${deployment_id}"
    done
  done

  deployments_json="$(curl --fail --silent --show-error --max-time 20 \
    "${api_base}/deployments?page=1&per_page=25" \
    -H "Authorization: Bearer ${api_token}")" ||
    fatal "could not verify Pages deployment inventory"
  total="$(jq -r '.result_info.total_count // (.result | length)' <<<"$deployments_json")"
  [[ "$total" == "1" ]] || fatal "Pages deployment inventory has ${total} entries after cleanup"
  [[ "$(jq -r '.result[0].id // empty' <<<"$deployments_json")" == "$canonical_id" ]] ||
    fatal "remaining Pages deployment is not canonical"
}

DEPLOY_TMP="$(mktemp -d -t lidi-pages-deploy.XXXXXX)"

LIVE_SHA="$(read_live_sha || true)"
if [[ "$LIVE_SHA" == "$GIT_SHA" ]]; then
  echo "[deploy-site] commit $GIT_SHA is already live; skipping duplicate deployment"
  verify_production
  prune_noncanonical_pages_deployments
  echo "[deploy-site] OK"
  exit 0
fi

AUTH_MODE=""
if [[ -f "$WRANGLER_OAUTH" ]]; then
  AUTH_MODE="oauth"
  unset CLOUDFLARE_API_TOKEN CF_API_TOKEN
elif [[ -f "$TOKEN_FILE" && -f "$META_FILE" ]]; then
  AUTH_MODE="api_token"
  export CLOUDFLARE_API_TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
  export CLOUDFLARE_ACCOUNT_ID="$(
    python3 -c "import json; print(json.load(open('$META_FILE'))['account_id'])"
  )"
else
  echo "FATAL: no Cloudflare authentication is configured" >&2
  exit 2
fi

COMMIT_MESSAGE="$(git log -1 --format=%s)"
echo "[deploy-site] deploying $GIT_SHA to Cloudflare Pages (auth=$AUTH_MODE)"
if ! ./node_modules/.bin/wrangler pages deploy dist \
  --project-name=lidi-solutions \
  --branch=main \
  --commit-hash="$GIT_SHA" \
  --commit-message="$COMMIT_MESSAGE" \
  --commit-dirty=false >"$DEPLOY_TMP/wrangler.log" 2>&1; then
  tail -40 "$DEPLOY_TMP/wrangler.log" >&2
  fatal "Cloudflare Pages deployment failed"
fi
tail -25 "$DEPLOY_TMP/wrangler.log"

verify_production
prune_noncanonical_pages_deployments
echo "[deploy-site] OK — live SHA matches $GIT_SHA"
