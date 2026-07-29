#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# guard-pass.sh — lightweight intraday PROTECTION pass (added 2026-07-24, operator-requested).
#
# The 5 agent passes leave 90-150 min gaps in which a stop breach waits unprotected and the
# operator hears nothing. This fills the gaps DETERMINISTICALLY — zero LLM cost, and it
# authors NO new entries: marks -> stop/falsifier enforcement (protective exits only) ->
# execution of approved exits -> fill sync -> one compact Telegram digest. The desk's edge
# is at 21-63d horizons; this adds protection latency + operator visibility, not trading.
#
# Host cron: 15 10,12 * * 1-5 and 30 14 * * 1-5 (gaps drop to <=75-90 min).

OC="/home/aaron/.openclaw"
PY="/usr/bin/python3"
LOG="$OC/logs/guard-pass.log"
TG_ACCOUNT="druck"; TG_TARGET="6043080629"
OPENCLAW_BIN="$("$OC/scripts/resolve-openclaw-bin.sh" 2>/dev/null || command -v openclaw || echo openclaw)"

ts()  { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "$(ts) $*" >>"$LOG"; }
tg()  { "$OPENCLAW_BIN" message send --channel telegram --account "$TG_ACCOUNT" -t "$TG_TARGET" -m "$*" >/dev/null 2>&1 || log "WARN: telegram send failed"; }

# only meaningful while the session is open (deterministic NYSE clock)
OPEN=$(cd "$OC/workspaces/trading-intel/scripts" && "$PY" -c "from connectors.marketdata import market_clock; print(1 if market_clock().get('is_open') else 0)" 2>/dev/null || echo 0)
if [[ "$OPEN" != "1" ]]; then log "market closed — skip"; exit 0; fi

log "===== guard pass start ====="
FAILED=""
step() { local label="$1"; shift
  if "$@" >>"$LOG" 2>&1; then log " ok: $label"
  else log " FAIL($?): $label"; FAILED="${FAILED:+$FAILED, }$label"; fi; }

step "mark_positions"     "$PY" "$OC/workspaces/developer/scripts/mark_positions.py"
step "enforce_falsifiers" "$PY" "$OC/workspaces/trader/scripts/enforce_falsifiers.py"
step "enforce_stops"      "$PY" "$OC/workspaces/trader/scripts/enforce_stops.py"
step "execute_intent"     "$PY" "$OC/workspaces/executor/scripts/execute_intent.py"
step "sync_fills"         "$PY" "$OC/workspaces/executor/scripts/sync_fills.py"

DIGEST=$("$PY" - <<'PYEOF'
import sqlite3
db = sqlite3.connect("file:/home/aaron/.openclaw/state/trading-intel.sqlite?mode=ro", uri=True)
db.row_factory = sqlite3.Row
eq = db.execute("SELECT equity, cash FROM book_equity WHERE book='desk' ORDER BY date DESC LIMIT 1").fetchone()
att = db.execute("SELECT trading_pl, cash_yield_pl FROM book_return_attribution WHERE book='desk' ORDER BY date DESC LIMIT 1").fetchone()
ints = {r["state"]: r["n"] for r in db.execute(
    "SELECT state, COUNT(*) n FROM trade_intents WHERE created_at >= date('now') GROUP BY state")}
npos = db.execute("SELECT COUNT(DISTINCT ticker) FROM positions WHERE state NOT IN ('closed') AND qty != 0").fetchone()[0]
movers = [f"{r['ticker']} {r['unrealized_pnl_pct']:+.1f}%" for r in db.execute(
    "SELECT ticker, unrealized_pnl_pct FROM positions WHERE state != 'closed' AND unrealized_pnl_pct IS NOT NULL "
    "ORDER BY ABS(unrealized_pnl_pct) DESC LIMIT 3")]
gross = db.execute("SELECT SUM(ABS(COALESCE(current_value, qty*cost_basis))) FROM positions WHERE state != 'closed'").fetchone()[0] or 0
# Pending rule_proposals: Aaron asked (2026-07-29) where to even SEE these —
# surface them in the digest whenever any await a decision.
props = [r["id"] for r in db.execute("SELECT id FROM rule_proposals WHERE status='proposed' ORDER BY created_at")]
ptxt = f" | 📋 proposals awaiting Aaron: {len(props)} ({', '.join(props[:3])}{'…' if len(props) > 3 else ''})" if props else ""
print(f"🛡 guard {'' if eq is None else f'— equity ${eq[0]:,.0f}'} | day P&L {'' if att is None else f'{att[0]:+.0f} trade / {att[1]:+.0f} yield'} | "
      f"{npos} names, gross ${gross:,.0f}, cash ${eq[1]:,.0f} | intents today: {ints or 'none'} | biggest: {', '.join(movers)}{ptxt}")
PYEOF
)
[[ -n "${FAILED:-}" ]] && DIGEST="$DIGEST | ⚠ failed: $FAILED"
tg "$DIGEST"
log "===== guard pass end (failed: ${FAILED:-none}) ====="
exit 0
