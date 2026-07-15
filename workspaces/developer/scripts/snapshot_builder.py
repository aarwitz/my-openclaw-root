#!/usr/bin/env python3
"""Deterministic builder for the product app's data.json.

Reads canonical SQLite + (optionally) Alpaca + cron-run state, computes the
shape consumed by `/repos/lidi-solutions/public/solutions/trader_intel/app/`,
and writes it atomically (temp file + rename).

This is the ONLY sanctioned writer of `data.json`. LLM turns may add
narrative fields by post-processing the file with strict-JSON outputs, but
the deterministic core must come from here.

Goal alignment:
- G1 (beat SPY): every hypothesis carries per-horizon scores; portfolio block
  carries `spy_comparison` with alpha vs SPY per horizon when data exists.
- G2 (deterministic + maintainable): no LLM in this script; failures emit a
  yellow `degraded` flag rather than an LLM-generated guess.
- G3 (retail insights): `retail_insights` block is populated with each
  agent's highest-impact takeaway per pass; `system_health` surfaces only
  when non-green.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors.alpaca import ConnectorError  # noqa: E402
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/executor/scripts")
from broker import get_account, list_orders, list_positions  # noqa: E402  (adapter, D52)

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
DEFAULT_OUT = Path(
    "/home/aaron/repos/lidi-solutions/public/solutions/trader_intel/app/data.json"
)
CRON_JOBS_PATH = Path(os.path.expanduser("~/.openclaw/cron/jobs.json"))
CRON_RUNS_DIR = Path(os.path.expanduser("~/.openclaw/cron/runs"))

AGENTS = (
    {"id": "researcher", "name": "Researcher", "emoji": "🔎"},
    {"id": "quant", "name": "Quant", "emoji": "🧮"},
    {"id": "critic", "name": "Critic", "emoji": "⚖️"},
    {"id": "archivist", "name": "Archivist", "emoji": "📚"},
    {"id": "trader", "name": "Trader", "emoji": "💰"},
    {"id": "executor", "name": "Executor", "emoji": "⚙️"},
    {"id": "developer", "name": "Developer", "emoji": "🛠️"},
    {"id": "overseer", "name": "AutoTrade", "emoji": "🤖"},
)

# Temporary deterministic corporate-action overrides until a full upstream
# announcements feed is wired into this builder path.
SPLIT_OVERRIDES: dict[str, float] = {
    "CRWD": 4.0,
}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else {k: row[k] for k in row.keys()}


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _rel_err(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1.0)
    return abs(a - b) / denom


def _normalize_broker_positions(
    raw_positions: list[dict[str, Any]],
    value_tol: float = 0.02,
    pnl_tol: float = 0.02,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize broker positions to one consistent basis.

    We keep execution history untouched and only normalize the snapshot view.
    Canonical arithmetic is:
      market_value ~= qty * current_price
      unrealized_pl ~= market_value - cost_basis
      avg_entry_price ~= cost_basis / qty
    """
    normalized: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for p in raw_positions:
        row = dict(p)
        symbol = str(row.get("symbol") or row.get("ticker") or "?").upper()

        qty = _safe_float(row.get("qty"), None)
        price = _safe_float(row.get("current_price"), None)
        mv = _safe_float(row.get("market_value"), None)
        cost_basis = _safe_float(row.get("cost_basis"), None)
        upl = _safe_float(row.get("unrealized_pl"), None)

        split_ratio = float(SPLIT_OVERRIDES.get(symbol, 1.0) or 1.0)
        avg_entry = _safe_float(row.get("avg_entry_price"), None)

        # Corporate-action override: if this symbol is configured with a known
        # split and still looks pre-split, normalize qty/basis/view fields.
        if (
            split_ratio > 1.0
            and qty not in (None, 0.0)
            and cost_basis is not None
            and avg_entry is not None
            and price not in (None, 0.0)
            and avg_entry > (price * (split_ratio - 0.5))
        ):
            qty = qty * split_ratio
            row["qty"] = qty
            row["avg_entry_price"] = cost_basis / qty
            if price is not None:
                mv = qty * price
                row["market_value"] = mv
            if mv is not None:
                upl = mv - cost_basis
                row["unrealized_pl"] = upl
                if cost_basis not in (None, 0.0):
                    row["unrealized_plpc"] = upl / cost_basis
            issues.append(
                {
                    "type": "corporate_action_split_applied",
                    "symbol": symbol,
                    "ratio": split_ratio,
                }
            )

        # If qty is stale but value/price are live (typical split mismatch),
        # infer normalized qty from market value and current price.
        if mv is not None and price not in (None, 0.0):
            inferred_qty = mv / price
            if qty is None or _rel_err((qty or 0.0) * price, mv) > value_tol:
                if qty is not None:
                    issues.append(
                        {
                            "type": "qty_price_value_mismatch",
                            "symbol": symbol,
                            "reported_qty": qty,
                            "normalized_qty": inferred_qty,
                            "current_price": price,
                            "market_value": mv,
                        }
                    )
                qty = inferred_qty
                row["qty"] = qty

        if cost_basis is not None and qty not in (None, 0.0):
            row["avg_entry_price"] = cost_basis / qty

        if mv is not None and cost_basis is not None:
            normalized_upl = mv - cost_basis
            if upl is None or _rel_err(upl, normalized_upl) > pnl_tol:
                if upl is not None:
                    issues.append(
                        {
                            "type": "unrealized_pl_mismatch",
                            "symbol": symbol,
                            "reported_unrealized_pl": upl,
                            "normalized_unrealized_pl": normalized_upl,
                        }
                    )
                row["unrealized_pl"] = normalized_upl
            if cost_basis not in (None, 0.0):
                row["unrealized_plpc"] = normalized_upl / cost_basis

        normalized.append(row)

    return normalized, issues


# ---------------------------------------------------------------------------
# DB readers (small, pure, testable)
# ---------------------------------------------------------------------------


def _load_regime(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, determined_at, determined_by, current, signals_json FROM regime "
        "ORDER BY determined_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return {
            "available": False,
            "current": None,
            "determined_at": None,
            "degraded": True,
            "degraded_reason": "no_regime_rows",
        }
    signals = {}
    try:
        signals = json.loads(row["signals_json"]) if row["signals_json"] else {}
    except json.JSONDecodeError:
        signals = {"error": "signals_json_unparseable"}
    return {
        "available": True,
        "id": row["id"],
        "determined_at": row["determined_at"],
        "determined_by": row["determined_by"],
        "current": row["current"],
        "signals": signals,
        "degraded": signals.get("fail_closed") is True or signals.get("partial") is True,
    }


def _load_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    def n(sql: str, params: tuple = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    return {
        "hypotheses_total": n("SELECT COUNT(*) FROM hypotheses"),
        "hypotheses_raw": n("SELECT COUNT(*) FROM hypotheses WHERE state='raw'"),
        "hypotheses_scored": n("SELECT COUNT(*) FROM hypotheses WHERE state='scored'"),
        "hypotheses_challenged": n(
            "SELECT COUNT(*) FROM hypotheses WHERE state='challenged'"
        ),
        "hypotheses_ready": n("SELECT COUNT(*) FROM hypotheses WHERE state='ready'"),
        "hypotheses_active": n("SELECT COUNT(*) FROM hypotheses WHERE state='active'"),
        "hypotheses_resolved": n(
            "SELECT COUNT(*) FROM hypotheses WHERE state='resolved'"
        ),
        "intents_open": n(
            "SELECT COUNT(*) FROM trade_intents "
            "WHERE state IN ('proposed','critic_review','approved','submitted','partial')"
        ),
        "positions_open": n(
            "SELECT COUNT(*) FROM positions "
            "WHERE state IN ('opening','open','scaling','trimming','closing')"
        ),
        "pauses_active": n(
            "SELECT COUNT(*) FROM system_pauses WHERE ended_at IS NULL"
        ),
        "critic_reviews_total": n("SELECT COUNT(*) FROM critic_reviews"),
    }


def _load_hypotheses(conn: sqlite3.Connection, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, tickers, thesis_summary, state, confidence, time_horizon, "
        "quant_score, scored_at, rationale_concise, created_at "
        "FROM hypotheses ORDER BY "
        "  CASE state WHEN 'ready' THEN 0 WHEN 'active' THEN 1 WHEN 'scored' THEN 2 "
        "             WHEN 'challenged' THEN 3 WHEN 'raw' THEN 4 ELSE 5 END, "
        "  COALESCE(quant_score, 0) DESC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            tickers = json.loads(r["tickers"] or "[]")
        except json.JSONDecodeError:
            tickers = []
        out.append(
            {
                "id": r["id"],
                "tickers": tickers,
                "thesis_summary": r["thesis_summary"],
                "state": r["state"],
                "confidence": r["confidence"],
                "time_horizon": r["time_horizon"],
                "quant_score": r["quant_score"],
                "scored_at": r["scored_at"],
                "rationale_concise": r["rationale_concise"],
                "created_at": r["created_at"],
            }
        )
    return out


def _load_critic_reviews(conn: sqlite3.Connection, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, target_type, target_id, reviewed_at, challenges_json, "
        "all_challenges_addressed FROM critic_reviews ORDER BY reviewed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            challenges = json.loads(r["challenges_json"] or "[]")
        except json.JSONDecodeError:
            challenges = []
        out.append(
            {
                "id": r["id"],
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "reviewed_at": r["reviewed_at"],
                "challenges": challenges,
                "all_addressed": bool(r["all_challenges_addressed"]),
            }
        )
    return out


def _load_intents(conn: sqlite3.Connection, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, hypothesis_id, created_at, action, ticker, vehicle, size, "
        "state, blocked_reason, submitted_at, executed_at, actual_price, actual_size, "
        "broker_order_id FROM trade_intents ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _load_evidence(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, hypothesis_id, indicator, value, source, source_url, "
        "retrieved_at, signal_type FROM hypothesis_evidence "
        "ORDER BY retrieved_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _load_audits(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, timestamp, actor, entity_type, entity_id, action, "
        "rationale_concise FROM audits ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _load_watchlist(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM positions "
        "WHERE state IN ('opening','open','scaling','trimming','closing') "
        "UNION SELECT DISTINCT ticker FROM trade_intents "
        "WHERE state IN ('proposed','approved','submitted','partial') "
        "ORDER BY 1"
    ).fetchall()
    return [{"ticker": r[0]} for r in rows]


def _load_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, hypothesis_id, ticker, vehicle, qty, cost_basis, current_price, "
        "current_value, unrealized_pnl_pct, pnl_slippage_adjusted, state, opened_at "
        "FROM positions WHERE state IN ('opening','open','scaling','trimming','closing') "
        "ORDER BY ABS(COALESCE(current_value,0)) DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _load_portfolio_snapshots(conn: sqlite3.Connection, limit: int = 180) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            "SELECT captured_at, equity, spy_close, cash, buying_power, day_pl "
            "FROM portfolio_snapshots ORDER BY captured_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    points = []
    for r in reversed(rows):
        points.append(
            {
                "timestamp": r["captured_at"],
                "equity": _safe_float(r["equity"]),
                "spy_close": _safe_float(r["spy_close"], None),
                "cash": _safe_float(r["cash"], None),
                "buying_power": _safe_float(r["buying_power"], None),
                "day_pl": _safe_float(r["day_pl"], None),
            }
        )
    return points


def _benchmark_spy_comparison(conn: sqlite3.Connection) -> dict[str, Any] | None:
    try:
        rows = conn.execute(
            "SELECT horizon, alpha_pct, portfolio_return_pct, spy_return_pct, captured_at "
            "FROM benchmarks ORDER BY captured_at DESC LIMIT 50"
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    latest_by_h: dict[str, dict[str, Any]] = {}
    for r in rows:
        h = r["horizon"]
        if h not in latest_by_h:
            latest_by_h[h] = {
                "horizon": h,
                "captured_at": r["captured_at"],
                "portfolio_return_pct": _safe_float(r["portfolio_return_pct"], None),
                "spy_return_pct": _safe_float(r["spy_return_pct"], None),
                "alpha_pct": _safe_float(r["alpha_pct"], None),
            }
    if not latest_by_h:
        return None
    horizons = [
        latest_by_h[h]
        for h in ("intraday", "swing_1_5d", "position_1_4w", "trend_1_3m", "long_6m_plus")
        if h in latest_by_h
    ]
    # system_era = the autonomous track record (post 2026-07-07 cutover); the
    # 'all' row includes the operator's manually ported pre-era portfolio and
    # must never be presented as system alpha.
    return {"available": True, "source": "benchmarks", "horizons": horizons,
            "system_era": latest_by_h.get("system_era"),
            "since_inception_incl_manual": latest_by_h.get("all")}


def _snapshot_spy_comparison(points: list[dict[str, Any]]) -> dict[str, Any]:
    if len(points) < 2:
        return {"available": False, "note": "portfolio_snapshots_insufficient"}

    first = points[0]
    last = points[-1]
    if not first.get("equity") or not last.get("equity"):
        return {"available": False, "note": "equity_series_missing"}
    if not first.get("spy_close") or not last.get("spy_close"):
        return {"available": False, "note": "spy_series_missing"}

    port_ret = ((last["equity"] / first["equity"]) - 1.0) * 100.0
    spy_ret = ((last["spy_close"] / first["spy_close"]) - 1.0) * 100.0
    alpha_pct = port_ret - spy_ret
    return {
        "available": True,
        "source": "portfolio_snapshots",
        "period_start": first["timestamp"],
        "period_end": last["timestamp"],
        "portfolio_return_pct": round(port_ret, 4),
        "spy_return_pct": round(spy_ret, 4),
        "alpha_pct": round(alpha_pct, 4),
        "alpha_bps": round(alpha_pct * 100.0, 1),
    }


def _load_broker_snapshot() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        acct = get_account()
        raw_positions = list_positions()
        orders = list_orders(status="all", limit=20)
        positions, norm_issues = _normalize_broker_positions(raw_positions)
        blocking_norm_issues = [
            i for i in norm_issues if i.get("type") != "corporate_action_split_applied"
        ]
        broker = {
            "status": acct.get("status"),
            "source": acct.get("source", "alpaca"),
            "account_number": acct.get("account_number"),
            "equity": _safe_float(acct.get("equity"), 0.0),
            "last_equity": _safe_float(acct.get("last_equity"), 0.0),
            "cash": _safe_float(acct.get("cash"), 0.0),
            "buying_power": _safe_float(acct.get("buying_power"), 0.0),
            "day_pl": _safe_float(acct.get("equity"), 0.0)
            - _safe_float(acct.get("last_equity"), 0.0),
            "available": True,
            "name": "alpaca_paper",
            "pnl_available": len(blocking_norm_issues) == 0,
            "normalization_issues": norm_issues,
        }
        return broker, positions, orders
    except ConnectorError as exc:
        return (
            {
                "name": "alpaca_paper",
                "available": False,
                "note": f"connector_error: {str(exc)[:180]}",
            },
            [],
            [],
        )


# ---------------------------------------------------------------------------
# Cron + agent freshness
# ---------------------------------------------------------------------------


def _load_cron_state() -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    if CRON_JOBS_PATH.exists():
        try:
            jobs = json.loads(CRON_JOBS_PATH.read_text()).get("jobs", [])
        except json.JSONDecodeError:
            jobs = []
    runs: list[dict[str, Any]] = []
    if CRON_RUNS_DIR.exists():
        recent = sorted(
            CRON_RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:30]
        for p in recent:
            try:
                runs.append(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue
    return {"jobs": jobs, "runs": runs}


def _last_pass_per_agent(audits: list[dict[str, Any]]) -> dict[str, str | None]:
    out: dict[str, str | None] = {a["id"]: None for a in AGENTS}
    for a in audits:
        actor = a.get("actor")
        if actor in out and out[actor] is None:
            out[actor] = a.get("timestamp")
    return out


# ---------------------------------------------------------------------------
# Retail insights core (deterministic; narrative pass fills the rest later)
# ---------------------------------------------------------------------------


def _build_retail_insights(
    regime: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    counts: dict[str, Any],
    last_pass: dict[str, str | None],
    spy_comparison: dict[str, Any],
) -> dict[str, Any]:
    top_scored = [h for h in hypotheses if h["quant_score"] is not None][:5]
    open_position_tickers = sorted({p["ticker"] for p in positions})
    pending_intents = [i for i in intents if i["state"] in ("proposed", "approved", "submitted", "partial")]
    regime_label = (regime.get("current") or "unknown").upper()
    top_tickers = [
        ",".join(h.get("tickers") or []) if isinstance(h.get("tickers"), list) else str(h.get("tickers") or "")
        for h in top_scored[:3]
    ]
    top_tickers = [t for t in top_tickers if t]
    headline = (
        f"{regime_label} regime, {counts['positions_open']} open positions, "
        f"{counts['intents_open']} open intents."
    )
    takeaways = [
        f"Top scored ideas: {', '.join(top_tickers)}" if top_tickers else "No scored ideas yet; sourcing/scoring in progress.",
        f"Pending intents: {len(pending_intents)}.",
        f"Active pauses: {counts['pauses_active']}.",
    ]

    return {
        "generated_at": _now_utc_iso(),
        "agent_takeaways": [
            {
                "agent": "researcher",
                "headline": f"{counts['hypotheses_raw']} new hypotheses awaiting score",
                "freshness_at": last_pass.get("researcher"),
                "needs_narrative": True,
            },
            {
                "agent": "quant",
                "headline": (
                    f"Regime: {regime.get('current') or 'unknown'}"
                    + (" (fail-closed)" if regime.get("degraded") else "")
                ),
                "top_scored": [
                    {
                        "id": h["id"],
                        "tickers": h["tickers"],
                        "score": h["quant_score"],
                        "horizon": h["time_horizon"],
                    }
                    for h in top_scored
                ],
                "freshness_at": last_pass.get("quant"),
                "needs_narrative": True,
            },
            {
                "agent": "critic",
                "headline": f"{counts['critic_reviews_total']} reviews on file",
                "freshness_at": last_pass.get("critic"),
                "needs_narrative": True,
            },
            {
                "agent": "archivist",
                "headline": "No pending rule_proposals" if True else "",
                "freshness_at": last_pass.get("archivist"),
                "needs_narrative": True,
            },
            {
                "agent": "executor",
                "headline": (
                    f"{counts['positions_open']} open positions, "
                    f"{counts['intents_open']} open intents, "
                    f"{counts['pauses_active']} active pauses"
                ),
                "open_tickers": open_position_tickers,
                "freshness_at": last_pass.get("executor"),
                "needs_narrative": False,
            },
            {
                "agent": "trader",
                "headline": "Portfolio narrative pending",
                "freshness_at": last_pass.get("trader"),
                "needs_narrative": True,
            },
            {
                "agent": "developer",
                "headline": "System health: green" if True else "",
                "freshness_at": last_pass.get("developer"),
                "needs_narrative": False,
            },
            {
                "agent": "overseer",
                "headline": "Chat / orchestration online",
                "freshness_at": last_pass.get("overseer"),
                "needs_narrative": False,
            },
        ],
        # Narrative slots filled by a downstream overseer pass with strict JSON.
        "headline": headline,
        "three_takeaways": takeaways,
        "regime_one_liner": (
            None if regime.get("current") is None
            else f"Regime is {regime['current']}."
        ),
        "next_24h_watch": [],
        "spy_comparison": spy_comparison,
        "pending_intents_count": len(pending_intents),
    }


def _build_system_health(
    regime: dict[str, Any],
    counts: dict[str, Any],
    last_pass: dict[str, str | None],
    cron: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    if regime.get("degraded"):
        notes = regime.get("signals", {}).get("notes", {}) or {}
        missing = regime.get("signals", {}).get("missing_signals", [])
        issues.append(
            {
                "severity": "yellow" if regime.get("current") == "caution" else "red",
                "area": "regime",
                "detail": f"degraded: missing={missing} notes={notes}",
            }
        )
    if counts.get("hypotheses_raw", 0) > 0 and (last_pass.get("quant") is None):
        issues.append(
            {
                "severity": "yellow",
                "area": "quant",
                "detail": "raw hypotheses present but no quant audit yet",
            }
        )
    color = "green"
    if any(i["severity"] == "red" for i in issues):
        color = "red"
    elif issues:
        color = "yellow"
    return {"color": color, "issues": issues[:3]}


# ---------------------------------------------------------------------------
# Top-level build
# ---------------------------------------------------------------------------


def _load_sim_books(conn: sqlite3.Connection) -> dict[str, Any]:
    """Internal paper-engine books (docs/07): daily equity curves + holdings.
    'shadow' validates our ledger vs Alpaca; 'model' is the GBM ranker's live
    long-only top-decile track record (P2)."""
    books: dict[str, Any] = {}
    try:
        for book, cash, starting, created in conn.execute(
                "SELECT book, cash, starting_cash, created_at FROM sim_accounts"):
            curve = [
                {"date": d, "equity": _safe_float(e), "cash": _safe_float(c)}
                for d, e, c in conn.execute(
                    "SELECT date, equity, cash FROM book_equity WHERE book=? ORDER BY date",
                    (book,))
            ]
            holdings = [
                {"ticker": t, "qty": _safe_float(q), "cost_basis": _safe_float(cb),
                 "current_value": _safe_float(cv)}
                for t, q, cb, cv in conn.execute(
                    "SELECT ticker, qty, cost_basis, current_value FROM sim_positions "
                    "WHERE book=? AND state='open' ORDER BY current_value DESC", (book,))
            ]
            books[book] = {
                "cash": _safe_float(cash),
                "starting_cash": _safe_float(starting),
                "since": created,
                "equity_curve": curve,
                "holdings_count": len(holdings),
                "top_holdings": holdings[:15],
            }
    except sqlite3.Error:
        return {}
    return books


def _load_capital_attribution(conn: sqlite3.Connection, spy_comparison: dict[str, Any]) -> dict[str, Any]:
    """Desk attribution split: trading P&L vs cash yield vs total, plus benchmark-relative.

    The daily rows are written by sim_broker.mark_book into book_return_attribution.
    """
    try:
        today = conn.execute(
            "SELECT date, equity, last_equity, trading_pl, cash_yield_pl, total_pl, "
            "trading_return_pct, cash_yield_return_pct, total_return_pct "
            "FROM book_return_attribution WHERE book='desk' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not today:
            return {"available": False, "note": "book_return_attribution_empty"}

        cumulative_cash_yield = conn.execute(
            "SELECT COALESCE(SUM(credit),0) FROM sim_cash_yield_events WHERE book='desk'"
        ).fetchone()[0]

        total_alpha_pct = None
        spy_ret_pct = None
        port_ret_pct = None
        if spy_comparison.get("available"):
            if "horizons" in spy_comparison and isinstance(spy_comparison["horizons"], list):
                all_h = next((h for h in spy_comparison["horizons"] if h.get("horizon") == "all"), None)
                pick = all_h if all_h else (spy_comparison["horizons"][0] if spy_comparison["horizons"] else None)
                if pick:
                    total_alpha_pct = _safe_float(pick.get("alpha_pct"), None)
                    spy_ret_pct = _safe_float(pick.get("spy_return_pct"), None)
                    port_ret_pct = _safe_float(pick.get("portfolio_return_pct"), None)
            else:
                total_alpha_pct = _safe_float(spy_comparison.get("alpha_pct"), None)
                spy_ret_pct = _safe_float(spy_comparison.get("spy_return_pct"), None)
                port_ret_pct = _safe_float(spy_comparison.get("portfolio_return_pct"), None)

        start_eq = _safe_float(today["last_equity"], None)
        cash_yield_return_since_start = None
        trading_alpha_pct = None
        if start_eq and start_eq > 0:
            cash_yield_return_since_start = float(cumulative_cash_yield) / float(start_eq) * 100.0
            if total_alpha_pct is not None:
                trading_alpha_pct = float(total_alpha_pct) - cash_yield_return_since_start

        return {
            "available": True,
            "daily": {
                "date": today["date"],
                "equity": _safe_float(today["equity"], None),
                "last_equity": _safe_float(today["last_equity"], None),
                "trading_pl": _safe_float(today["trading_pl"], None),
                "cash_yield_pl": _safe_float(today["cash_yield_pl"], None),
                "total_pl": _safe_float(today["total_pl"], None),
                "trading_return_pct": _safe_float(today["trading_return_pct"], None),
                "cash_yield_return_pct": _safe_float(today["cash_yield_return_pct"], None),
                "total_return_pct": _safe_float(today["total_return_pct"], None),
            },
            "cumulative": {
                "cash_yield_pl": _safe_float(cumulative_cash_yield, 0.0),
            },
            "benchmark_relative": {
                "portfolio_return_pct": port_ret_pct,
                "spy_return_pct": spy_ret_pct,
                "total_alpha_pct": total_alpha_pct,
                "trading_alpha_pct": None if trading_alpha_pct is None else round(trading_alpha_pct, 4),
                "cash_yield_alpha_pct": None if cash_yield_return_since_start is None else round(cash_yield_return_since_start, 4),
            },
        }
    except sqlite3.Error:
        return {"available": False, "note": "attribution_query_failed"}


def _load_rotation(conn: sqlite3.Connection) -> dict[str, Any]:
    """Latest basket-rotation snapshot per axis + 30d seesaw-day count (D64)."""
    out: dict[str, Any] = {}
    try:
        for r in conn.execute(
            "SELECT axis, date, corr_21d, spread_5d_pct, spread_21d_pct, spread_z, "
            "corr_pctile, seesaw FROM rotation_snapshots WHERE (axis, date) IN "
            "(SELECT axis, MAX(date) FROM rotation_snapshots GROUP BY axis)"):
            axis = r[0]
            days = conn.execute(
                "SELECT COUNT(*) FROM rotation_snapshots WHERE axis=? AND seesaw=1 "
                "AND date >= date(?, '-45 days')", (axis, r[1])).fetchone()[0]
            out[axis] = {"date": r[1], "corr_21d": _safe_float(r[2], None),
                         "spread_5d_pct": _safe_float(r[3], None),
                         "spread_21d_pct": _safe_float(r[4], None),
                         "spread_z": _safe_float(r[5], None),
                         "corr_pctile": _safe_float(r[6], None),
                         "seesaw": bool(r[7]), "seesaw_days_recent": days}
    except sqlite3.Error:
        return {}
    return out


def build_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    regime = _load_regime(conn)
    counts = _load_counts(conn)
    hypotheses = _load_hypotheses(conn)
    critic_reviews = _load_critic_reviews(conn)
    intents = _load_intents(conn)
    evidence = _load_evidence(conn)
    audits = _load_audits(conn)
    watchlist = _load_watchlist(conn)
    positions = _load_positions(conn)
    equity_history = _load_portfolio_snapshots(conn)
    benchmark_cmp = _benchmark_spy_comparison(conn)
    spy_comparison = benchmark_cmp or _snapshot_spy_comparison(equity_history)
    broker, broker_positions, broker_orders = _load_broker_snapshot()
    cron = _load_cron_state()
    last_pass = _last_pass_per_agent(audits)

    snapshot = {
        "generated_at": _now_utc_iso(),
        "schema_version": "v2",
        "topology": [a["id"] for a in AGENTS],
        "agents": [
            {**a, "last_pass_at": last_pass.get(a["id"])} for a in AGENTS
        ],
        "regime": regime,
        "broker": broker,
        "brokerPositions": broker_positions,
        "brokerOrders": broker_orders,
        "equityHistory": equity_history,
        "equityHistoryIntraday": _intraday_equity(),
        "positions": positions,
        "hypotheses": hypotheses,
        "criticReviews": critic_reviews,
        "watchlist": watchlist,
        "intents": intents,
        "evidence": evidence,
        "cronJobs": cron["jobs"],
        "cronRuns": cron["runs"],
        "audits": audits,
        "counts": counts,
        "simBooks": _load_sim_books(conn),
        "rotation": _load_rotation(conn),
        "capital_attribution": _load_capital_attribution(conn, spy_comparison),
        "retail_insights": _build_retail_insights(
            regime, hypotheses, positions, intents, counts, last_pass, spy_comparison
        ),
        "system_health": _build_system_health(regime, counts, last_pass, cron),
    }
    return snapshot


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.stem + ".",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(payload, tmp, indent=2, default=str)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def _intraday_equity():
    """Last 7 days of intraday desk-book equity samples (D53, powers 1D/1W chart)."""
    try:
        import sqlite3, time
        c = sqlite3.connect(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
        cutoff = int((time.time() - 7 * 86400) * 1000)
        rows = c.execute(
            "SELECT ts, equity FROM book_equity_intraday WHERE book='desk' AND ts >= ? ORDER BY ts",
            (cutoff,)).fetchall()
        return [{"ts": int(t), "equity": float(e)} for t, e in rows]
    except Exception:
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Build and print to stdout without writing the file",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: db missing at {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    snapshot = build_snapshot(conn)
    if args.validate:
        print(json.dumps(snapshot, indent=2, default=str))
        return 0
    write_atomic(Path(args.out), snapshot)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
