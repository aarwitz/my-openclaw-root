#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
# publish-trader-intel.sh
#
# Pushes the freshly written trader-intel data.json to Cloudflare via wrangler.
# Runs vite build first so dist/ is consistent with public/.
#
# Auth strategy (in priority order):
#   1. Wrangler OAuth session at ~/.config/.wrangler/config/default.toml
#      (auto-refreshes via refresh_token). Preferred.
#   2. CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID from openclaw credentials.
#      Requires Workers:Edit scope on the token.
#
# Exit codes: 0 published, 1 build/deploy failed, 2 environment missing.
set -uo pipefail

LIDI_REPO="${TRADER_INTEL_REPO:-${LIDI:-$HOME/repos/lidi-solutions}}"
WRANGLER_OAUTH="$HOME/.config/.wrangler/config/default.toml"
CRED_DIR="${HOME}/.openclaw/credentials/cloudflare"
TOKEN_FILE="${CRED_DIR}/account-token"
META_FILE="${CRED_DIR}/account-meta.json"

if [[ ! -d "$LIDI_REPO" ]]; then
  echo "FATAL: lidi-solutions repo missing at $LIDI_REPO" >&2
  exit 2
fi

AUTH_MODE=""
if [[ -f "$WRANGLER_OAUTH" ]]; then
  AUTH_MODE="oauth"
  # Important: unset API token env vars so wrangler uses OAuth instead.
  unset CLOUDFLARE_API_TOKEN CF_API_TOKEN
elif [[ -f "$TOKEN_FILE" && -f "$META_FILE" ]]; then
  AUTH_MODE="api_token"
  export CLOUDFLARE_API_TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
  export CLOUDFLARE_ACCOUNT_ID="$(python3 -c "import json;print(json.load(open('$META_FILE'))['account_id'])")"
else
  echo "FATAL: no wrangler OAuth at $WRANGLER_OAUTH and no API token at $TOKEN_FILE" >&2
  exit 2
fi

cd "$LIDI_REPO" || { echo "FATAL: cd $LIDI_REPO failed" >&2; exit 2; }

if [[ ! -x "$LIDI_REPO/node_modules/.bin/wrangler" ]]; then
  echo "[publish] installing repo node_modules (one-time)..."
  if ! npm install --no-audit --no-fund --silent; then
    echo "FATAL: npm install failed" >&2
    exit 1
  fi
fi

echo "[publish] auth_mode=$AUTH_MODE"
echo "[publish] vite build"
if ! npm run build --silent; then
  echo "FATAL: vite build failed" >&2
  exit 1
fi

echo "[publish] wrangler pages deploy dist (project=lidi-solutions)"
# Capture full output to a tempfile so we keep the actual exit code AND can show
# the last lines without piping through tail (which would mask the exit code).
DEPLOY_LOG="$(mktemp -t publish-trader-intel.XXXXXX.log)"
if ./node_modules/.bin/wrangler pages deploy dist --project-name=lidi-solutions --branch=main --commit-dirty=true >"$DEPLOY_LOG" 2>&1; then
  tail -25 "$DEPLOY_LOG"
  rm -f "$DEPLOY_LOG"
  # Post-deploy verification (2026-07-07): the deploy is not "ok" until the live
  # API actually serves sane chart ranges. audit-trader-live.mjs fails on empty
  # or malformed ranges — the exact class of bug that shipped silently before.
  echo "[publish] post-deploy verify: audit-trader-live.mjs"
  sleep 15  # let the Pages deployment propagate before probing
  if ! node "$LIDI_REPO/scripts/audit-trader-live.mjs"; then
    echo "FATAL: deploy went out but /api/trader-live failed post-deploy audit" >&2
    exit 1
  fi
  echo "[publish] ok"
  exit 0
fi
tail -40 "$DEPLOY_LOG" >&2
rm -f "$DEPLOY_LOG"
echo "FATAL: wrangler pages deploy failed" >&2
exit 1
