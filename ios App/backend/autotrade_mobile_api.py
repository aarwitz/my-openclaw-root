#!/usr/bin/env python3
"""
AutoTrade Mobile API — lightweight Flask server that bridges the iOS app
to trading-intel.sqlite and the Robinhood session managed by the gateway.

Run:
    python autotrade_mobile_api.py
    # or via the gateway's governed runner:
    OPENCLAW_REQUIRE_WRAPPER_NO_AUTORUN=1 python autotrade_mobile_api.py

Endpoints:
    GET  /api/signals                 list approved/submitted/filled intents
    GET  /api/signals/<id>            single signal detail
    POST /api/signals/<id>/execute    place Robinhood order for this signal
    GET  /api/portfolio               live Robinhood portfolio
    GET  /api/performance             win-rate / avg-return stats
    POST /api/robinhood/link          store session credentials
    GET  /api/robinhood/status        linked account info
    POST /api/robinhood/unlink        clear stored session
    POST /api/push/register           store APNs device token for push
"""

import json
import os
import sqlite3
import traceback
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, abort

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = Path(os.environ.get(
    "TRADING_INTEL_DB",
    Path(__file__).parents[2] / "state" / "trading-intel.sqlite"
))
API_KEY = os.environ.get("AUTOTRADE_MOBILE_API_KEY", "")      # empty = no auth
PORT    = int(os.environ.get("OPENCLAW_MOBILE_API_PORT", 8765))
STATE_DIR = Path(__file__).parents[2] / "state"
PUSH_TOKENS_FILE = STATE_DIR / "mobile_push_tokens.json"
RH_SESSION_FILE  = STATE_DIR / "mobile_rh_session.json"       # stored locally, gitignored

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if API_KEY:
            provided = request.headers.get("X-AutoTrade-Key", "")
            if provided != API_KEY:
                abort(401)
        return f(*args, **kwargs)
    return wrapper

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Signal endpoints
# ---------------------------------------------------------------------------

@app.get("/api/signals")
@require_api_key
def list_signals():
    states_param = request.args.get("state", "approved,submitted,filled")
    limit = min(int(request.args.get("limit", 50)), 200)
    states = [s.strip() for s in states_param.split(",")]
    placeholders = ",".join("?" for _ in states)

    with get_db() as db:
        rows = db.execute(f"""
            SELECT
                ti.id,
                ti.ticker,
                ti.action,
                ti.vehicle,
                ti.state,
                ti.entry_price_target,
                ti.stop_rule,
                ti.time_horizon,
                ti.triggered_by,
                ti.edge_scorecard_json,
                ti.quant_score,         -- from expression_candidates via join below
                ti.created_at,
                ti.submitted_at,
                ti.executed_at          AS filled_at,
                ti.actual_price,
                ti.actual_size,
                ti.modeled_fill_price,
                h.thesis_summary,
                h.confidence,
                h.quant_score           AS hyp_quant_score
            FROM trade_intents ti
            JOIN hypotheses h ON h.id = ti.hypothesis_id
            WHERE ti.state IN ({placeholders})
            ORDER BY ti.created_at DESC
            LIMIT ?
        """, [*states, limit]).fetchall()

    signals = []
    for r in rows:
        d = row_to_dict(r)
        d["quant_score"] = d.get("quant_score") or d.get("hyp_quant_score")
        signals.append(d)

    return jsonify(signals)


@app.get("/api/signals/<signal_id>")
@require_api_key
def get_signal(signal_id):
    with get_db() as db:
        row = db.execute("""
            SELECT
                ti.*,
                h.thesis_summary,
                h.confidence,
                h.quant_score           AS hyp_quant_score,
                h.rationale_concise
            FROM trade_intents ti
            JOIN hypotheses h ON h.id = ti.hypothesis_id
            WHERE ti.id = ?
        """, [signal_id]).fetchone()
    if not row:
        abort(404)
    return jsonify(row_to_dict(row))


@app.post("/api/signals/<signal_id>/execute")
@require_api_key
def execute_signal(signal_id):
    """Place a Robinhood market order for this signal on behalf of the linked user."""
    body = request.get_json(force=True)
    quantity = float(body.get("quantity", 1))

    with get_db() as db:
        row = db.execute(
            "SELECT ticker, action, vehicle, state FROM trade_intents WHERE id = ?",
            [signal_id]
        ).fetchone()
    if not row:
        abort(404)
    if row["state"] not in ("approved", "submitted"):
        return jsonify({"success": False, "error": f"Signal is in state '{row['state']}', not executable"}), 400

    session = _load_rh_session()
    if not session:
        return jsonify({"success": False, "error": "No Robinhood session — link account first"}), 400

    try:
        import robin_stocks.robinhood as rh
        rh.authentication.set_login_state(True)
        rh.authentication.update_session(session["token"])

        if row["action"] in ("open", "add"):
            result = rh.orders.order_buy_market(row["ticker"], quantity)
        else:
            result = rh.orders.order_sell_market(row["ticker"], quantity)

        order_id = result.get("id")
        return jsonify({"success": True, "order_id": order_id})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

# ---------------------------------------------------------------------------
# Portfolio endpoints
# ---------------------------------------------------------------------------

@app.get("/api/portfolio")
@require_api_key
def get_portfolio():
    session = _load_rh_session()
    if not session:
        return jsonify({"error": "No Robinhood session"}), 401

    try:
        import robin_stocks.robinhood as rh
        rh.authentication.set_login_state(True)
        rh.authentication.update_session(session["token"])

        profile = rh.profiles.load_portfolio_profile()
        positions_raw = rh.account.build_holdings()

        positions = []
        for ticker, data in (positions_raw or {}).items():
            qty = float(data.get("quantity", 0))
            avg = float(data.get("average_buy_price", 0))
            price = float(data.get("price", 0))
            equity = float(data.get("equity", qty * price))
            pct = float(data.get("percentage", 0))
            total_return = (price - avg) * qty

            # check if this position matches a filled signal
            signal_id = None
            with get_db() as db:
                row = db.execute(
                    "SELECT id FROM trade_intents WHERE ticker = ? AND state = 'filled' ORDER BY executed_at DESC LIMIT 1",
                    [ticker]
                ).fetchone()
                if row:
                    signal_id = row["id"]

            positions.append({
                "id": ticker,
                "ticker": ticker,
                "name": data.get("name", ticker),
                "quantity": qty,
                "average_buy_price": avg,
                "current_price": price,
                "equity": equity,
                "percent_change": pct,
                "total_return": total_return,
                "signal_id": signal_id,
            })

        equity = float(profile.get("equity", 0))
        prev_equity = float(profile.get("adjusted_equity_previous_close", equity))
        day_change = equity - prev_equity
        day_change_pct = (day_change / prev_equity * 100) if prev_equity else 0
        extended_hours_equity = float(profile.get("extended_hours_equity") or equity)
        total_return = float(profile.get("total_return") or 0)
        total_return_pct = float(profile.get("total_return_percentage") or 0)

        return jsonify({
            "equity": equity,
            "cash": float(profile.get("withdrawable_amount", 0)),
            "day_change": day_change,
            "day_change_pct": day_change_pct,
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "positions": positions,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        return jsonify({"error": traceback.format_exc()}), 500


@app.get("/api/performance")
@require_api_key
def get_performance():
    days = int(request.args.get("days", 30))
    with get_db() as db:
        total = db.execute(
            "SELECT COUNT(*) FROM trade_intents WHERE state IN ('filled','submitted','approved')"
        ).fetchone()[0]
        filled = db.execute(
            "SELECT id, ticker, actual_price, modeled_fill_price FROM trade_intents WHERE state = 'filled' ORDER BY executed_at DESC LIMIT 200"
        ).fetchall()

    wins = 0
    returns = []
    best = worst = None

    for row in filled:
        actual = row["actual_price"]
        modeled = row["modeled_fill_price"]
        if actual and modeled and modeled > 0:
            ret = (actual - modeled) / modeled * 100
            returns.append(ret)
            if ret > 0:
                wins += 1
            if best is None or ret > best["return_pct"]:
                best = {"ticker": row["ticker"], "return_pct": ret, "closed_at": None}
            if worst is None or ret < worst["return_pct"]:
                worst = {"ticker": row["ticker"], "return_pct": ret, "closed_at": None}

    win_rate = wins / len(returns) if returns else 0
    avg_return = sum(returns) / len(returns) if returns else 0

    return jsonify({
        "total_signals": total,
        "followed_signals": len(filled),
        "win_rate": win_rate,
        "avg_return": avg_return,
        "best_trade": best,
        "worst_trade": worst,
        "period_days": days,
    })

# ---------------------------------------------------------------------------
# Robinhood auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/robinhood/link")
@require_api_key
def link_robinhood():
    body = request.get_json(force=True)
    username = body.get("username", "")
    password = body.get("password", "")
    mfa_code = body.get("mfa_code")

    if not username or not password:
        return jsonify({"success": False, "error": "username and password required"}), 400

    try:
        import robin_stocks.robinhood as rh
        kwargs = {"store_session": False, "mfa_code": mfa_code} if mfa_code else {"store_session": False}
        result = rh.login(username, password, **kwargs)

        # robin_stocks raises an exception or returns a dict on MFA prompt
        if isinstance(result, dict) and result.get("mfa_required"):
            return jsonify({"success": False, "requires_mfa": True})

        token = rh.authentication.get_token()
        account = rh.account.load_account_profile()
        account_number = account.get("account_number", "unknown")

        _save_rh_session({"token": token, "username": username, "account_number": account_number})
        return jsonify({"success": True, "requires_mfa": False, "account_number": account_number})
    except Exception as exc:
        err = str(exc)
        if "mfa" in err.lower() or "two" in err.lower():
            return jsonify({"success": False, "requires_mfa": True})
        return jsonify({"success": False, "error": err, "requires_mfa": False}), 400


@app.get("/api/robinhood/status")
@require_api_key
def robinhood_status():
    session = _load_rh_session()
    if not session:
        return jsonify({"is_linked": False})
    return jsonify({
        "is_linked": True,
        "username": session.get("username"),
        "account_number": session.get("account_number"),
        "linked_at": session.get("linked_at"),
        "buying_power": None,   # populate lazily if needed
        "portfolio_value": None,
    })


@app.post("/api/robinhood/unlink")
@require_api_key
def unlink_robinhood():
    if RH_SESSION_FILE.exists():
        RH_SESSION_FILE.unlink()
    return jsonify({"success": True})

# ---------------------------------------------------------------------------
# Push notification registration
# ---------------------------------------------------------------------------

@app.post("/api/push/register")
@require_api_key
def register_push():
    body = request.get_json(force=True)
    token = body.get("device_token", "")
    platform = body.get("platform", "ios")
    if not token:
        return jsonify({"success": False, "error": "device_token required"}), 400

    tokens = _load_push_tokens()
    tokens[token] = {"platform": platform, "registered_at": datetime.now(timezone.utc).isoformat()}
    _save_push_tokens(tokens)
    return jsonify({"success": True})

# ---------------------------------------------------------------------------
# Session / token helpers
# ---------------------------------------------------------------------------

def _load_rh_session():
    if RH_SESSION_FILE.exists():
        return json.loads(RH_SESSION_FILE.read_text())
    return None

def _save_rh_session(data):
    data["linked_at"] = datetime.now(timezone.utc).isoformat()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RH_SESSION_FILE.write_text(json.dumps(data, indent=2))

def _load_push_tokens():
    if PUSH_TOKENS_FILE.exists():
        return json.loads(PUSH_TOKENS_FILE.read_text())
    return {}

def _save_push_tokens(tokens):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PUSH_TOKENS_FILE.write_text(json.dumps(tokens, indent=2))

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    db_ok = DB_PATH.exists()
    return jsonify({
        "ok": db_ok,
        "db": str(DB_PATH),
        "db_exists": db_ok,
        "ts": datetime.now(timezone.utc).isoformat(),
    })

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"AutoTrade Mobile API starting on port {PORT}")
    print(f"DB: {DB_PATH}")
    print(f"Auth: {'enabled' if API_KEY else 'DISABLED — set AUTOTRADE_MOBILE_API_KEY to secure'}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
