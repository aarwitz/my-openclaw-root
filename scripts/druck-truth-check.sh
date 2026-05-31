#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
# druck-truth-check.sh
# Deterministic skill health + evidence audit for Druck.
# Writes results to /tmp/druck-truth-check.txt
# Safe to run at any time (read-only; no mutations).
set -u

TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
REPORT_FILE="/tmp/druck-truth-check.txt"
CRED="/home/aaron/.openclaw/credentials"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

PASS_LINES=()
FAIL_LINES=()
WARN_LINES=()

add_pass() { PASS_COUNT=$((PASS_COUNT + 1)); PASS_LINES+=("  ✓ $1"); }
add_fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); FAIL_LINES+=("  ✗ $1"); }
add_warn() { WARN_COUNT=$((WARN_COUNT + 1)); WARN_LINES+=("  ⚠ $1"); }

classify_error() {
  local s; s="$(echo "$1" | tr '[:upper:]' '[:lower:]')"
  if echo "$s" | grep -qE "rate.limit|quota|429"; then echo "rate_limit"
  elif echo "$s" | grep -qE "invalid_grant|token has been expired or revoked"; then echo "auth_expired"
  elif echo "$s" | grep -qE "unknown flag|flag provided but not defined"; then echo "cli_syntax_mismatch"
  elif echo "$s" | grep -qE "auth|401|403|unauthorized|forbidden|invalid.key|apikey"; then echo "auth"
  elif echo "$s" | grep -qE "timeout|timed.out|network|connect|dns|unreachable"; then echo "network"
  elif echo "$s" | grep -qE "not found|no such file or directory"; then echo "missing_dep"
  else echo "other"; fi
}

# ── 1) Finnhub ───────────────────────────────────────────────────────────────
FINNHUB_KEY="$(jq -r '."api key"' "${CRED}/finnhub-api.json" 2>/dev/null || true)"
if [[ -z "$FINNHUB_KEY" || "$FINNHUB_KEY" == "null" ]]; then
  add_fail "finnhub: credential unreadable"
else
  FH_OUT="$(curl -sf --max-time 10 \
    "https://finnhub.io/api/v1/quote?symbol=SPY&token=${FINNHUB_KEY}" 2>&1 || true)"
  if echo "$FH_OUT" | jq -e '.c > 0' >/dev/null 2>&1; then
    SPY_PRICE="$(echo "$FH_OUT" | jq -r '.c')"
    add_pass "finnhub: quote ok — SPY close=${SPY_PRICE}"
  else
    REASON="$(classify_error "$FH_OUT")"
    add_fail "finnhub: quote failed (${REASON}) — ${FH_OUT:0:120}"
  fi
fi

# ── 2) NewsAPI AI ─────────────────────────────────────────────────────────────
NEWS_KEY="$(jq -r '."API key"' "${CRED}/news-api-ai.json" 2>/dev/null || true)"
if [[ -z "$NEWS_KEY" || "$NEWS_KEY" == "null" ]]; then
  add_fail "newsapi-ai: credential unreadable"
else
  NEWS_BODY="{\"action\":\"getArticles\",\"keyword\":\"market\",\"articlesCount\":1,\"apiKey\":\"${NEWS_KEY}\"}"
  NEWS_OUT="$(curl -sf --max-time 15 \
    -H "Content-Type: application/json" \
    -d "$NEWS_BODY" \
    "https://eventregistry.org/api/v1/article/getArticles" 2>&1 || true)"
  if echo "$NEWS_OUT" | jq -e '.articles.results | length > 0' >/dev/null 2>&1; then
    add_pass "newsapi-ai: endpoint reachable, articles returned"
  elif echo "$NEWS_OUT" | jq -e '.error' >/dev/null 2>&1; then
    ERR="$(echo "$NEWS_OUT" | jq -r '.error')"
    REASON="$(classify_error "$ERR")"
    add_fail "newsapi-ai: api error (${REASON}) — ${ERR:0:100}"
  else
    REASON="$(classify_error "$NEWS_OUT")"
    add_fail "newsapi-ai: unexpected response (${REASON}) — ${NEWS_OUT:0:120}"
  fi
fi

# ── 3) Alpaca Paper ──────────────────────────────────────────────────────────
ALPACA_KEY="$(jq -r '."api key"' "${CRED}/alpaca-api.json" 2>/dev/null || true)"
ALPACA_SECRET="$(jq -r '.secret' "${CRED}/alpaca-api.json" 2>/dev/null || true)"
ALPACA_BASE="https://paper-api.alpaca.markets/v2"

if [[ -z "$ALPACA_KEY" || "$ALPACA_KEY" == "null" ]]; then
  add_fail "alpaca: credential unreadable"
else
  ALPACA_OUT="$(curl -sf --max-time 10 \
    -H "APCA-API-KEY-ID: ${ALPACA_KEY}" \
    -H "APCA-API-SECRET-KEY: ${ALPACA_SECRET}" \
    "${ALPACA_BASE}/account" 2>&1 || true)"
  if echo "$ALPACA_OUT" | jq -e '.status == "ACTIVE"' >/dev/null 2>&1; then
    NAV="$(echo "$ALPACA_OUT" | jq -r '.equity // .portfolio_value // "unknown"')"
    add_pass "alpaca paper: account ACTIVE — NAV=\$${NAV}"
  elif echo "$ALPACA_OUT" | jq -e '.status' >/dev/null 2>&1; then
    STATUS="$(echo "$ALPACA_OUT" | jq -r '.status')"
    add_warn "alpaca paper: account status=${STATUS}"
  else
    REASON="$(classify_error "$ALPACA_OUT")"
    add_fail "alpaca paper: account probe failed (${REASON}) — ${ALPACA_OUT:0:120}"
  fi
fi

# ── 4) FMP ───────────────────────────────────────────────────────────────────
FMP_KEY="$(jq -r '."api key"' "${CRED}/financial-modeling-prep-api.json" 2>/dev/null || true)"
if [[ -z "$FMP_KEY" || "$FMP_KEY" == "null" ]]; then
  add_fail "fmp: credential unreadable"
else
  FMP_OUT="$(curl -sf --max-time 10 \
    "https://financialmodelingprep.com/api/v3/quote/SPY?apikey=${FMP_KEY}" 2>&1 || true)"
  if echo "$FMP_OUT" | jq -e '.[0].price > 0' >/dev/null 2>&1; then
    add_pass "fmp: quote ok"
  elif echo "$FMP_OUT" | jq -e '.["Error Message"] // .message' >/dev/null 2>&1; then
    ERR="$(echo "$FMP_OUT" | jq -r '.["Error Message"] // .message')"
    REASON="$(classify_error "$ERR")"
    add_fail "fmp: error (${REASON}) — ${ERR:0:100}"
  else
    REASON="$(classify_error "$FMP_OUT")"
    add_fail "fmp: unexpected response (${REASON}) — ${FMP_OUT:0:120}"
  fi
fi

# ── 5) Massive (Polygon) ─────────────────────────────────────────────────────
MASSIVE_KEY="$(jq -r '."api key" // .apiKey // .key // empty' "${CRED}/massive-api.json" 2>/dev/null || true)"
if [[ -z "$MASSIVE_KEY" || "$MASSIVE_KEY" == "null" ]]; then
  add_warn "massive: credential unreadable (key field unknown — check massive-api.json structure)"
else
  MASSIVE_OUT="$(curl -sf --max-time 10 \
    "https://api.polygon.io/v2/aggs/ticker/SPY/prev?apiKey=${MASSIVE_KEY}" 2>&1 || true)"
  if echo "$MASSIVE_OUT" | jq -e '.resultsCount > 0' >/dev/null 2>&1; then
    add_pass "massive (polygon): prev-day agg ok"
  elif echo "$MASSIVE_OUT" | jq -e '.status == "OK"' >/dev/null 2>&1; then
    add_pass "massive (polygon): status OK (no results — possibly weekend/holiday)"
  else
    REASON="$(classify_error "$MASSIVE_OUT")"
    add_fail "massive (polygon): probe failed (${REASON}) — ${MASSIVE_OUT:0:120}"
  fi
fi

# ── 6) Google Sheets adapter path (gog CLI + auth + read probe) ────────────
if ! command -v gog >/dev/null 2>&1; then
  add_fail "gog/sheets: gog CLI missing on PATH"
else
  GOG_HELP="$(gog sheets get --help 2>&1 || true)"
  if echo "$GOG_HELP" | grep -qiE "<spreadsheetId>\s+<range>"; then
    add_pass "gog/sheets: CLI syntax supports positional <spreadsheetId> <range>"
  else
    add_warn "gog/sheets: could not confirm expected get syntax from help output"
  fi

  GOG_AUTH_OUT="$(gog auth list --check --no-input 2>&1 || true)"
  if echo "$GOG_AUTH_OUT" | grep -qiE "invalid_grant|token has been expired or revoked"; then
    add_fail "gog/sheets: auth expired/revoked (invalid_grant)"
  elif echo "$GOG_AUTH_OUT" | grep -qiE "\btrue\b|ok|healthy|ready|works|authorized"; then
    add_pass "gog/sheets: auth check passed"
  elif [[ -n "$GOG_AUTH_OUT" ]]; then
    REASON="$(classify_error "$GOG_AUTH_OUT")"
    add_warn "gog/sheets: auth check inconclusive (${REASON})"
  else
    add_warn "gog/sheets: auth check inconclusive (empty output)"
  fi

  SHEETS_READ_OUT="$(gog sheets get 19LPX1xGCme4umn22GN4Z7WBQxGZBWWcysDjM6JEW-D4 Candidates!A1:B2 -j 2>&1 || true)"
  if echo "$SHEETS_READ_OUT" | jq -e 'type=="array" or (.values? != null)' >/dev/null 2>&1; then
    add_pass "gog/sheets: read probe ok (Candidates!A1:B2)"
  else
    REASON="$(classify_error "$SHEETS_READ_OUT")"
    add_fail "gog/sheets: read probe failed (${REASON}) — ${SHEETS_READ_OUT:0:120}"
  fi
fi

# ── 7) Druck session freshness ───────────────────────────────────────────────
# Prefer canonical trader path; keep legacy druck path as fallback.
SESSIONS_FILE=""
for candidate in \
  "/home/aaron/.openclaw/agents/trader/sessions/sessions.json" \
  "/home/aaron/.openclaw/agents/druck/sessions/sessions.json"; do
  if [[ -f "$candidate" ]]; then
    SESSIONS_FILE="$candidate"
    break
  fi
done

if [[ -n "$SESSIONS_FILE" ]]; then
  NOW_MS="$(date +%s%3N)"
  # Get the most recent updatedAt across all druck sessions
  LAST_UPDATE_MS="$(jq '[.. | .updatedAt? // empty] | max // 0' "$SESSIONS_FILE" 2>/dev/null || echo 0)"
  if [[ "$LAST_UPDATE_MS" -gt 0 ]]; then
    AGE_HOURS=$(( (NOW_MS - LAST_UPDATE_MS) / 3600000 ))
    if [[ "$AGE_HOURS" -le 24 ]]; then
      add_pass "druck session: last activity ${AGE_HOURS}h ago (fresh; ${SESSIONS_FILE})"
    elif [[ "$AGE_HOURS" -le 72 ]]; then
      add_warn "druck session: last activity ${AGE_HOURS}h ago (stale — no Druck response in 3 days; ${SESSIONS_FILE})"
    else
      add_fail "druck session: last activity ${AGE_HOURS}h ago (very stale — Druck may be inactive; ${SESSIONS_FILE})"
    fi
  else
    add_warn "druck session: could not determine last activity (${SESSIONS_FILE})"
  fi
else
  add_warn "druck session: sessions.json not found (checked trader and legacy druck paths)"
fi

# ── 8) Candidates sheet freshness (proxy via Drive/Notes modification time) ──
# Best-effort: check if Druck has a Notes or Candidates output file updated this week
NOTES_CACHE="/home/aaron/.openclaw/workspaces/druck/memory"
if [[ -d "$NOTES_CACHE" ]]; then
  RECENT_NOTES="$(find "$NOTES_CACHE" -name "*.md" -newer "$NOTES_CACHE" -mtime -7 2>/dev/null | head -1)"
  if [[ -n "$RECENT_NOTES" ]]; then
    add_pass "druck memory: notes updated within last 7 days"
  else
    add_warn "druck memory: no notes updated in last 7 days — Druck may not have run weekly research"
  fi
fi

# ── Report ────────────────────────────────────────────────────────────────────
{
  echo "DRUCK_TRUTH_CHECK — ${TS}"
  echo "═══════════════════════════════════════════════"
  echo "SUMMARY: ${PASS_COUNT} pass / ${WARN_COUNT} warn / ${FAIL_COUNT} fail"
  if [[ $FAIL_COUNT -eq 0 && $WARN_COUNT -eq 0 ]]; then
    echo "STATUS: CLEAN — all Druck skills healthy and data sources reachable"
  elif [[ $FAIL_COUNT -eq 0 ]]; then
    echo "STATUS: PARTIAL — skills reachable but some warnings require review"
  else
    echo "STATUS: DEGRADED — ${FAIL_COUNT} skill(s) failing; Druck claims from these sources are UNVERIFIABLE"
  fi
  echo ""
  if [[ ${#PASS_LINES[@]} -gt 0 ]]; then
    echo "PASS:"
    printf '%s\n' "${PASS_LINES[@]}"
    echo ""
  fi
  if [[ ${#WARN_LINES[@]} -gt 0 ]]; then
    echo "WARN:"
    printf '%s\n' "${WARN_LINES[@]}"
    echo ""
  fi
  if [[ ${#FAIL_LINES[@]} -gt 0 ]]; then
    echo "FAIL:"
    printf '%s\n' "${FAIL_LINES[@]}"
    echo ""
    echo "TRUST GUIDANCE:"
    echo "  Any Druck claim citing a failed source above should be treated as UNVERIFIED."
    echo "  Do not accept buy_ready/conditional_buy recommendations backed only by failing sources."
    echo "  Open a trading-skill-failure Task Manager issue and require Druck to rerun after fix."
  fi
} > "$REPORT_FILE"

cat "$REPORT_FILE"
