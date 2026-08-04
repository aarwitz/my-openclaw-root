#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# push-trader-data.sh
#
# Fast data-only publish: regenerates the trader-intel snapshot (data.json) and
# pushes it to the Cloudflare KV namespace TRADER_DATA, which the Pages Function
# /api/trader-data serves to the app. This decouples data freshness from code
# deploys (deploy-lidi-solutions.sh) — a KV put takes seconds, a full deploy
# takes minutes and counts against Pages deploy limits.
#
# Intended cadence: every 10 minutes during US market hours (host cron), plus
# it runs harmlessly off-hours (snapshot just re-reads the same state).
#
# Auth: same strategy as deploy-lidi-solutions.sh — wrangler OAuth session
# preferred, credentials/cloudflare API token as fallback.
#
# Exit codes: 0 pushed, 1 snapshot/push failed, 2 environment missing.

LIDI_REPO="${TRADER_INTEL_REPO:-${LIDI:-$HOME/repos/lidi-solutions}}"
KV_NAMESPACE_ID="bc7ab40d92b04c8f90e9448b4896689a"
RUNTIME_DIR="${TRADER_INTEL_RUNTIME_DIR:-$HOME/.openclaw/state/trader-intel-snapshot}"
DATA_JSON="$RUNTIME_DIR/data.json"
CANONICAL_STATE_JSON="$RUNTIME_DIR/canonical-state.json"
WRANGLER_OAUTH="$HOME/.config/.wrangler/config/default.toml"
CRED_DIR="$HOME/.openclaw/credentials/cloudflare"
TOKEN_FILE="$CRED_DIR/account-token"
META_FILE="$CRED_DIR/account-meta.json"

if [[ ! -d "$LIDI_REPO" ]]; then
  echo "FATAL: lidi-solutions repo missing at $LIDI_REPO" >&2
  exit 2
fi
mkdir -p "$RUNTIME_DIR"

# Direct host-cron publishes share the same writer lock as trader/guard/learning
# passes. A publish invoked by trader-pass inherits the already-held lock.
if [[ "${TRADING_MONEY_LOCK_HELD:-0}" != "1" ]]; then
  exec 9>"$HOME/.openclaw/state/trading-money-path.lock"
  if ! flock -w 60 9; then
    echo "FATAL: timed out waiting for trading money-path lock" >&2
    exit 1
  fi
  export TRADING_MONEY_LOCK_HELD=1
fi

if [[ -f "$WRANGLER_OAUTH" ]]; then
  unset CLOUDFLARE_API_TOKEN CF_API_TOKEN
elif [[ -f "$TOKEN_FILE" && -f "$META_FILE" ]]; then
  export CLOUDFLARE_API_TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"
  export CLOUDFLARE_ACCOUNT_ID="$(python3 -c "import json;print(json.load(open('$META_FILE'))['account_id'])")"
else
  echo "FATAL: no wrangler OAuth and no API token" >&2
  exit 2
fi

cd "$LIDI_REPO" || exit 2

# D53: sample the desk book's intraday equity before each snapshot so the
# 1D/1W chart has real points. Fail closed: publishing a stale account mark
# behind a fresh generated_at is forbidden.
if ! python3 "$HOME/.openclaw/workspaces/executor/scripts/sim_broker.py" mark --book desk >/dev/null; then
  echo "FATAL: internal paper mark failed; refusing KV publish" >&2
  exit 1
fi

# Rebuild canonical state and then project the public v4 contract. The two
# stages use different files so the presentation stage cannot erase or launder
# the canonical safety fields it consumes.
if ! python3 "$HOME/.openclaw/workspaces/developer/scripts/snapshot_builder.py" --out "$CANONICAL_STATE_JSON"; then
  echo "FATAL: canonical internal-paper snapshot failed" >&2
  exit 1
fi

echo "[push-data] project snapshot"
if ! TRADER_INTEL_OUT_DIR="$RUNTIME_DIR" TRADER_INTEL_SKIP_DIST=1 \
    TRADER_INTEL_BASE_FILE="$CANONICAL_STATE_JSON" \
    node scripts/snapshot-trader-intel.mjs; then
  echo "FATAL: snapshot-trader-intel.mjs failed" >&2
  exit 1
fi

if [[ ! -s "$DATA_JSON" ]]; then
  echo "FATAL: $DATA_JSON missing or empty after snapshot" >&2
  exit 1
fi
# Sanity: valid JSON with the expected contract before it goes anywhere.
if ! python3 -c "
import json,sys
d=json.load(open('$DATA_JSON'))
assert d.get('contract_version','').startswith('trader-intel/'), 'bad contract'
assert d.get('generated_at'), 'no generated_at'
assert (d.get('topology') or {}).get('topology_version') == 'v5', 'bad topology'
assert (d.get('broker') or {}).get('source') == 'sim', 'bad broker source'
assert 'account_number' not in (d.get('broker') or {}), 'raw account identifier'
assert (d.get('broker') or {}).get('pnl_available') is True, 'inconsistent broker pnl'
assert not ((d.get('broker') or {}).get('normalization_issues') or []), 'position normalization issues'
bp = d.get('brokerPositions') or []
# direction is set unconditionally by the canonical enrichment — its absence means
# the enrichment never ran and Day P&L would render \$0.00 across the app.
assert (not bp) or all(p.get('direction') for p in bp), 'brokerPositions missing canonical enrichment'
for key in ('selectionFunnel', 'predictionReplay', 'inventoryLineage'):
    assert key in d, f'missing canonical safety field: {key}'
"; then
  echo "FATAL: data.json failed contract sanity check — not pushing" >&2
  exit 1
fi

if ! python3 "$HOME/.openclaw/workspaces/developer/scripts/audit_app_snapshot.py" \
    --path "$DATA_JSON" --no-write >/dev/null; then
  echo "FATAL: canonical app audit is red; refusing KV publish" >&2
  exit 1
fi

if [[ "${TRADER_DATA_DRY_RUN:-0}" == "1" ]]; then
  echo "[push-data] dry-run ok; validated snapshot not uploaded"
  exit 0
fi

# Pin wrangler: bare `npx wrangler` resolves via PATH — under host cron that hit the
# (broken) system npm while interactive shells hit nvm, so pushes failed ONLY under cron
# (2026-07-23, ~4.5h of stale app data). Prefer the repo's pinned binary; fall back to
# nvm npx, then bare npx as a last resort.
WRANGLER_BIN="/home/aaron/repos/lidi-solutions/node_modules/.bin/wrangler"
if [[ ! -x "$WRANGLER_BIN" ]]; then
  NVM_NPX=$(ls -1 "$HOME"/.nvm/versions/node/*/bin/npx 2>/dev/null | sort -V | tail -1)
  WRANGLER_BIN="${NVM_NPX:+$NVM_NPX wrangler}"
  WRANGLER_BIN="${WRANGLER_BIN:-npx wrangler}"
fi

echo "[push-data] kv put ($(stat -c%s "$DATA_JSON") bytes) via $WRANGLER_BIN"
if ! $WRANGLER_BIN kv key put data.json --path "$DATA_JSON" \
      --namespace-id "$KV_NAMESPACE_ID" --remote 2>&1 | tail -2; then
  echo "FATAL: wrangler kv put failed" >&2
  exit 1
fi

echo "[push-data] ok $(date -u +%FT%TZ)"
