#!/usr/bin/env python3
"""AutoTrade telemetry plugin for the improvement kernel (AGENTIC_SYSTEM.md).

Reads the desk store READ-ONLY and prints ranked deficiency signals as JSON on
stdout, following the kernel's telemetry contract. The PM pass files the top
unaddressed signal as a TM issue tagged drag:<id> and later verifies, against a
fresh run of this report, that merged fixes actually shrank the signal.

Deliberately small: a signal belongs here only if it is (a) measured from the
store, never inferred, and (b) actionable as a code change. Judgment-quality
problems belong to the desk's fast loop (mechanism updates), not this report.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import feature_store
import gate_evaluator
import worldmodel
import brier_contributors

DB_PATH = "/home/aaron/.openclaw/state/trading-intel.sqlite"
RISK_GATE_PATH = Path("/home/aaron/.openclaw/workspaces/risk/scripts/gate_risk_intents.py")

# Coin-flip Brier for a binary outcome is 0.25; the desk should beat it.
BRIER_COINFLIP = 0.25
BLOCK_LOOKBACK_DAYS = 14
BRIER_LOOKBACK_DAYS = 30
STALE_PREDICTION_DAYS = 21
LEGACY_ARTIFACT_ACTION = "mark_historical_artifacts"
RISK_REDUCING_ACTIONS = {"exit", "trim"}
OPEN_POSITION_STATES = ("opening", "open", "scaling", "trimming", "closing")
PENDING_INTENT_STATES = ("approved", "submitted", "partial")
EXITING_INTENT_STATES = ("proposed", "critic_review", "risk_review", "approved", "submitted", "partial")

# --- Money-awareness (the objective) --------------------------------------
# The report was blind to P&L/deployment/SPY-alpha, so the improvement loop
# could only file pipeline-hygiene issues. These make the objective a measured,
# always-present part of the report (the `objective` block) plus ONE
# code-actionable signal (idle-cash-drag). Per the 2026-07-22 decisions we
# MEASURE the alpha/selection numbers (visible, not auto-filed) and only file
# the idle-cash drag, whose dominant cause is idea-supply (an origination
# throughput code gap), not a reason to loosen risk.
OBJECTIVE_HORIZONS = ("position_1_4w", "system_era", "all")  # trailing month, system era, inception
# book_return_attribution only begins at the 2026-07-07 system epoch, so a 30d
# inclusive window currently captures the entire history — reported whole, rather
# than an arbitrary shorter cut that is sensitive to a single outlier day.
ATTR_WINDOW_DAYS = 30
IDLE_DRAG_PCT_FLOOR = 25.0         # only surface idle cash when it is structurally material
IDLE_DRAG_USD_FLOOR = 5_000.0      # ...and a material dollar amount


def normalize_block_reason(reason: str) -> str:
    """Collapse per-intent detail so identical failure classes group together."""
    reason = (reason or "").strip()
    if reason.startswith("risk:no sizing headroom"):
        return "risk:no sizing headroom (name=N, gross=N)"
    reason = re.sub(r"\[[^\]]*\]", "", reason)
    reason = re.sub(r"\d+(\.\d+)?", "N", reason)
    return reason[:90] or "(no reason recorded)"


def parse_failed_gates(reason: str) -> list[str]:
    reason = (reason or "").strip()
    if not reason.startswith("gates_failed:"):
        return []
    core = reason.split("|", 1)[0]
    core = re.sub(r"\[[^\]]*\]", "", core)
    gates = core.removeprefix("gates_failed:").split(",")
    return [gate.strip() for gate in gates if gate.strip()]


def classify_gate_block(row: sqlite3.Row, reevaluated: dict | None) -> dict:
    reason = (row["blocked_reason"] or "").strip()
    action = (row["action"] or "").strip()
    original_failed = parse_failed_gates(reason)
    original_key = "gates_failed:" + ",".join(original_failed) if original_failed else normalize_block_reason(reason)

    if action in RISK_REDUCING_ACTIONS and reevaluated and reevaluated.get("all_pass"):
        return {
            "active": False,
            "class_key": f"legacy_false_positive:{original_key}",
            "summary_key": original_key,
            "evidence": (
                f"intent {row['id']} action={action} was blocked as {original_key} "
                "but current gate stack now passes it (legacy pre-D47 risk-reducing false positive)"
            ),
        }

    failed_now = (
        reevaluated.get("failed_gates", [])
        if reevaluated and reevaluated.get("failed_gates") is not None
        else original_failed
    )
    class_key = "gates_failed:" + ",".join(failed_now) if failed_now else original_key
    gate_details = {
        gate["name"]: gate.get("detail", "")
        for gate in (reevaluated or {}).get("gates", [])
        if not gate.get("pass")
    }
    detail_bits = []
    for name in failed_now:
        detail = gate_details.get(name)
        if detail:
            detail_bits.append(f"{name} -> {detail}")
    evidence = (
        f"intent {row['id']} action={action} blocked as {class_key}; "
        + ("; ".join(detail_bits) if detail_bits else f"recorded_reason={reason}")
    )
    return {
        "active": True,
        "class_key": class_key,
        "summary_key": class_key,
        "evidence": evidence,
    }


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "unknown"


@lru_cache(maxsize=1)
def current_max_positions() -> int | None:
    try:
        text = RISK_GATE_PATH.read_text()
    except OSError:
        return None
    match = re.search(r"^MAX_POSITIONS\s*=\s*(\d+)", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


@lru_cache(maxsize=1)
def current_risk_pct_limits() -> dict[str, float | None]:
    try:
        text = RISK_GATE_PATH.read_text()
    except OSError:
        return {"max_name_pct": None, "max_gross_pct": None}
    out: dict[str, float | None] = {"max_name_pct": None, "max_gross_pct": None}
    for key, const in (("max_name_pct", "MAX_NAME_PCT"), ("max_gross_pct", "MAX_GROSS_PCT")):
        match = re.search(rf"^{const}\s*=\s*([0-9.]+)", text, flags=re.MULTILINE)
        if match:
            out[key] = float(match.group(1))
    return out


def latest_equity(cur: sqlite3.Cursor, fallback: float | None = None) -> float | None:
    try:
        row = cur.execute(
            "SELECT equity FROM capital_efficiency_snapshots ORDER BY as_of DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        row = None
    if row and row["equity"] is not None:
        return float(row["equity"])
    return fallback


def latest_risk_review_limits(conn: sqlite3.Connection, intent_id: str) -> dict:
    try:
        row = conn.execute(
            "SELECT reviewed_at, limits_json, breaches_json FROM risk_reviews "
            "WHERE target_id=? ORDER BY reviewed_at DESC LIMIT 1",
            (intent_id,),
        ).fetchone()
    except sqlite3.Error:
        return {}
    if not row:
        return {}
    try:
        limits = json.loads(row["limits_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        limits = {}
    return {
        "reviewed_at": row["reviewed_at"],
        "limits": limits,
        "breaches_json": row["breaches_json"],
    }


def trade_intent_sizing_inputs(conn: sqlite3.Connection, intent_id: str) -> dict:
    try:
        row = conn.execute(
            "SELECT ticker, size, entry_price_target FROM trade_intents WHERE id=?",
            (intent_id,),
        ).fetchone()
    except sqlite3.Error:
        return {}
    if not row:
        return {}
    try:
        price = float(row["entry_price_target"] or 0.0)
    except (TypeError, ValueError):
        price = 0.0
    size = float(row["size"] or 0.0)
    return {
        "ticker": (row["ticker"] or "").upper(),
        "size": size,
        "entry_price": price,
        "notional": round(abs(size * price), 2),
    }


def legacy_artifact_disposition(conn: sqlite3.Connection, summary_key: str) -> dict | None:
    try:
        row = conn.execute(
            "SELECT id, timestamp, after_state, rationale_concise FROM audits "
            "WHERE actor='developer' AND entity_type='trade_intents' "
            "AND entity_id=? AND action=? "
            "ORDER BY timestamp DESC LIMIT 1",
            (summary_key, LEGACY_ARTIFACT_ACTION),
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "after_state": row["after_state"],
        "rationale": row["rationale_concise"],
    }


def _position_notional(row: sqlite3.Row) -> float:
    val = row["current_value"]
    if val is None:
        val = float(row["qty"] or 0.0) * float(row["cost_basis"] or 0.0)
    return abs(float(val or 0.0))


def current_gross_sizing_snapshot(conn: sqlite3.Connection, ticker: str, price: float, fallback_equity: float | None) -> dict:
    limits = current_risk_pct_limits()
    equity = latest_equity(conn.cursor(), fallback=fallback_equity)
    if equity is None or limits["max_gross_pct"] is None or limits["max_name_pct"] is None:
        return {"available": False, "reason": "risk_limits_or_equity_unavailable"}
    try:
        positions = conn.execute(
            "SELECT ticker, current_value, qty, cost_basis FROM positions "
            f"WHERE state IN ({','.join('?' * len(OPEN_POSITION_STATES))})",
            OPEN_POSITION_STATES,
        ).fetchall()
        pending = conn.execute(
            "SELECT id, ticker, action, state, size, entry_price_target FROM trade_intents "
            f"WHERE state IN ({','.join('?' * len(PENDING_INTENT_STATES))})",
            PENDING_INTENT_STATES,
        ).fetchall()
    except sqlite3.Error as exc:
        return {"available": False, "reason": f"current_snapshot_unavailable:{exc}"}

    sym = (ticker or "").upper()
    position_gross = 0.0
    name_existing = 0.0
    top_positions = []
    for row in positions:
        notional = _position_notional(row)
        position_gross += notional
        if (row["ticker"] or "").upper() == sym:
            name_existing += notional
        top_positions.append({"ticker": (row["ticker"] or "").upper(), "notional": round(notional, 2)})

    pending_new_risk = []
    pending_risk_reducing = []
    for row in pending:
        action = (row["action"] or "").lower()
        notional = abs(float(row["size"] or 0.0) * float(row["entry_price_target"] or 0.0))
        item = {
            "id": row["id"],
            "ticker": (row["ticker"] or "").upper(),
            "action": action,
            "state": row["state"],
            "notional": round(notional, 2),
        }
        if action in RISK_REDUCING_ACTIONS:
            pending_risk_reducing.append(item)
            continue
        pending_new_risk.append(item)
        if item["ticker"] == sym:
            name_existing += notional

    pending_new_risk_notional = sum(float(row["notional"] or 0.0) for row in pending_new_risk)
    gross = position_gross + pending_new_risk_notional
    gross_cap = float(limits["max_gross_pct"] or 0.0) * equity
    name_cap = float(limits["max_name_pct"] or 0.0) * equity
    concurrent = concurrent_name_snapshot(conn)
    max_positions = current_max_positions()
    candidate_is_new_name = name_existing <= 0
    slot_ok = (
        max_positions is None
        or not candidate_is_new_name
        or concurrent["count"] < max_positions
    )
    gross_headroom = max(0.0, gross_cap - gross)
    name_headroom = max(0.0, name_cap - name_existing)
    can_buy_one_share = bool(price > 0 and gross_headroom >= price and name_headroom >= price and slot_ok)
    return {
        "available": True,
        "equity": round(equity, 2),
        "gross_cap": round(gross_cap, 2),
        "current_gross": round(gross, 2),
        "gross_headroom": round(gross_headroom, 2),
        "name_cap": round(name_cap, 2),
        "name_existing": round(name_existing, 2),
        "name_headroom": round(name_headroom, 2),
        "candidate_is_new_name": candidate_is_new_name,
        "concurrent_names": concurrent["count"],
        "max_positions": max_positions,
        "slot_ok": slot_ok,
        "can_buy_one_share": can_buy_one_share,
        "position_gross": round(position_gross, 2),
        "pending_new_risk_notional": round(pending_new_risk_notional, 2),
        "pending_risk_reducing_notional": round(sum(float(row["notional"] or 0.0) for row in pending_risk_reducing), 2),
        "top_positions": sorted(top_positions, key=lambda item: -item["notional"])[:4],
        "pending_new_risk_intents": pending_new_risk[:4],
        "pending_risk_reducing_intents": pending_risk_reducing[:4],
    }


def concurrent_name_snapshot(conn: sqlite3.Connection) -> dict:
    contributors: dict[str, set[str]] = {}
    try:
        for row in conn.execute(
            "SELECT UPPER(ticker) AS ticker, state FROM positions "
            f"WHERE state IN ({','.join('?' * len(OPEN_POSITION_STATES))})",
            OPEN_POSITION_STATES,
        ):
            ticker = row["ticker"]
            if not ticker:
                continue
            contributors.setdefault(ticker, set()).add(f"position:{row['state']}")
    except sqlite3.Error:
        pass
    try:
        for row in conn.execute(
            "SELECT UPPER(ticker) AS ticker, state, action FROM trade_intents "
            f"WHERE state IN ({','.join('?' * len(PENDING_INTENT_STATES))})",
            PENDING_INTENT_STATES,
        ):
            ticker = row["ticker"]
            if not ticker:
                continue
            contributors.setdefault(ticker, set()).add(f"intent:{row['state']}:{(row['action'] or '').lower()}")
    except sqlite3.Error:
        pass
    try:
        exiting = {
            row["ticker"]
            for row in conn.execute(
                "SELECT DISTINCT UPPER(ticker) AS ticker FROM trade_intents "
                "WHERE action IN ('exit','trim') "
                f"AND state IN ({','.join('?' * len(EXITING_INTENT_STATES))})",
                EXITING_INTENT_STATES,
            )
            if row["ticker"]
        }
    except sqlite3.Error:
        exiting = set()
    active_slots = [
        {"ticker": ticker, "sources": sorted(sources)}
        for ticker, sources in sorted(contributors.items())
        if ticker not in exiting
    ]
    return {"count": len(active_slots), "active_slots": active_slots, "exiting_tickers": sorted(exiting)}


def summarize_slots(slots: list[dict], limit: int = 4) -> str:
    if not slots:
        return "none"
    shown = []
    for slot in slots[:limit]:
        shown.append(f"{slot['ticker']}[{','.join(slot.get('sources', []))}]")
    remaining = len(slots) - len(shown)
    if remaining > 0:
        shown.append(f"+{remaining} more")
    return ", ".join(shown)


def parse_concurrent_name_reason(reason: str) -> tuple[int | None, int | None]:
    match = re.search(r"concurrent_names=(\d+)\s*>=\s*cap=(\d+)", reason or "")
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _first_ticker(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not data or not isinstance(data, list):
        return None
    ticker = data[0]
    return str(ticker).upper() if ticker else None


def _window_available(prices: list[dict], entry_iso: str, horizon_days: int) -> bool:
    dates = [bar["t"] for bar in prices]
    index = 0
    while index < len(dates) and dates[index] < entry_iso:
        index += 1
    return index < len(dates) and index + horizon_days < len(dates)


def count_stale_predictions(cur: sqlite3.Cursor) -> int:
    rows = cur.execute(
        "SELECT p.id, p.predicted_at, p.horizon, h.tickers "
        "FROM predictions p JOIN hypotheses h ON h.id = p.hypothesis_id "
        "WHERE p.realized_outcome IS NULL AND p.resolved_at IS NULL "
        "AND p.predicted_at < datetime('now', ?) "
        "ORDER BY p.predicted_at ASC",
        (f"-{STALE_PREDICTION_DAYS} days",),
    ).fetchall()
    if not rows:
        return 0
    spy_prices = feature_store._prices("SPY", 4000)
    ticker_cache: dict[str, list[dict]] = {}
    stale = 0
    for row in rows:
        horizon_days = worldmodel.HORIZON_DAYS.get(row["horizon"], 15)
        if not _window_available(spy_prices, row["predicted_at"][:10], horizon_days):
            continue
        ticker = _first_ticker(row["tickers"])
        if not ticker:
            stale += 1
            continue
        if ticker not in ticker_cache:
            try:
                ticker_cache[ticker] = feature_store._prices(ticker, 4000)
            except Exception:
                ticker_cache[ticker] = []
        if _window_available(ticker_cache[ticker], row["predicted_at"][:10], horizon_days):
            stale += 1
        else:
            stale += 1
    return stale


def classify_risk_block(row: sqlite3.Row) -> dict:
    reason = (row["blocked_reason"] or "").strip()
    historical_count, historical_cap = parse_concurrent_name_reason(reason)
    if reason.startswith("risk:no sizing headroom"):
        review = latest_risk_review_limits(row["conn"], row["id"])
        limits = review.get("limits", {})
        sizing = trade_intent_sizing_inputs(row["conn"], row["id"])
        current = current_gross_sizing_snapshot(
            row["conn"],
            sizing.get("ticker", ""),
            float(sizing.get("entry_price") or 0.0),
            fallback_equity=limits.get("equity"),
        )
        gross_attr = limits.get("gross_exposure_attribution") or {}
        sizing_attr = limits.get("sizing_block_attribution") or {}
        blocked_at_bits = [
            f"reviewed_at={review.get('reviewed_at') or 'unknown'}",
            f"requested={sizing.get('size', 'unknown')}@{sizing.get('entry_price', 'unknown')} notional={sizing.get('notional', 'unknown')}",
            (
                "blocked_at "
                f"gross={limits.get('current_gross')}/{limits.get('gross_cap')} "
                f"gross_headroom={limits.get('gross_headroom')} "
                f"name={limits.get('name_existing')}/{limits.get('name_cap')} "
                f"name_headroom={limits.get('name_headroom')}"
            ),
        ]
        if gross_attr:
            blocked_at_bits.append(
                "portfolio_at_block "
                f"positions=${gross_attr.get('position_gross')} "
                f"pending_new_risk=${gross_attr.get('pending_new_risk_notional')} "
                f"pending_exits=${gross_attr.get('pending_risk_reducing_notional')} "
                f"open_orders={len(gross_attr.get('open_orders') or [])}"
            )
        else:
            blocked_at_bits.append("portfolio_at_block=legacy limits_json only")
        if sizing_attr:
            blocked_at_bits.append(
                "sizing_at_block "
                f"min_share=${sizing_attr.get('min_one_share_notional')} "
                f"binding={','.join(sizing_attr.get('binding_breaches') or [])}"
            )
        if current.get("available"):
            blocked_at_bits.append(
                "live_now "
                f"gross={current['current_gross']}/{current['gross_cap']} "
                f"gross_headroom={current['gross_headroom']} "
                f"name_headroom={current['name_headroom']} "
                f"slots={current['concurrent_names']}/{current['max_positions']} "
                f"pending_new_risk=${current['pending_new_risk_notional']} "
                f"pending_exits=${current['pending_risk_reducing_notional']} "
                f"can_buy_one_share={current['can_buy_one_share']}"
            )
        else:
            blocked_at_bits.append(f"live_now_unavailable={current.get('reason')}")

        active = not bool(current.get("can_buy_one_share"))
        class_prefix = "" if active else "legacy_false_positive:"
        evidence_prefix = "still binding" if active else "resolved under current portfolio state"
        return {
            "active": active,
            "class_key": f"{class_prefix}{normalize_block_reason(reason)}",
            "summary_key": normalize_block_reason(reason),
            "evidence": (
                f"intent {row['id']} action={row['action']} blocked as {reason}; "
                f"{evidence_prefix}; " + "; ".join(blocked_at_bits)
            ),
        }

    if not reason.startswith("risk:concurrent_names="):
        return {
            "active": True,
            "class_key": normalize_block_reason(reason),
            "summary_key": normalize_block_reason(reason),
            "evidence": f"intent {row['id']} action={row['action']} blocked as {reason}",
        }

    try:
        snapshot = concurrent_name_snapshot(row["conn"])
    except sqlite3.Error as exc:
        return {
            "active": True,
            "class_key": normalize_block_reason(reason),
            "summary_key": normalize_block_reason(reason),
            "evidence": f"intent {row['id']} action={row['action']} blocked as {reason}; attribution_unavailable={exc}",
        }

    current_cap = current_max_positions()
    slot_summary = summarize_slots(snapshot["active_slots"])
    if current_cap is not None and snapshot["count"] < current_cap:
        return {
            "active": False,
            "class_key": f"legacy_false_positive:{normalize_block_reason(reason)}",
            "summary_key": normalize_block_reason(reason),
            "evidence": (
                f"intent {row['id']} action={row['action']} blocked as {reason} "
                f"but live concurrent_names={snapshot['count']}/{current_cap}; slots={slot_summary}"
            ),
        }

    current_label = (
        f"live concurrent_names={snapshot['count']}/{current_cap}"
        if current_cap is not None
        else f"live concurrent_names={snapshot['count']}"
    )
    return {
        "active": True,
        "class_key": normalize_block_reason(reason),
        "summary_key": normalize_block_reason(reason),
        "evidence": (
            f"intent {row['id']} action={row['action']} blocked as {reason}; "
            f"{current_label}; slots={slot_summary}; historical={historical_count}/{historical_cap}"
        ),
    }


def _latest_benchmarks(cur: sqlite3.Cursor) -> dict[str, sqlite3.Row]:
    """Latest captured row per horizon (the SPY scoreboard)."""
    out: dict[str, sqlite3.Row] = {}
    try:
        rows = cur.execute(
            """SELECT horizon, period_start, period_end, portfolio_return_pct,
                      spy_return_pct, alpha_pct, sharpe_estimate
               FROM benchmarks b
               WHERE captured_at = (
                   SELECT MAX(captured_at) FROM benchmarks b2 WHERE b2.horizon = b.horizon)"""
        ).fetchall()
        out = {row["horizon"]: row for row in rows}
    except sqlite3.Error as exc:
        print(f"WARN: benchmarks read skipped: {exc}", file=sys.stderr)
    return out


def _latest_capital_efficiency(cur: sqlite3.Cursor) -> sqlite3.Row | None:
    try:
        return cur.execute(
            "SELECT * FROM capital_efficiency_snapshots ORDER BY as_of DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        print(f"WARN: capital_efficiency read skipped: {exc}", file=sys.stderr)
        return None


def _trailing_book_attribution(cur: sqlite3.Cursor, days: int = ATTR_WINDOW_DAYS, book: str = "desk") -> dict | None:
    """Deterministic split of the deployed sleeve's realized P&L (trading vs cash-yield)."""
    try:
        row = cur.execute(
            """SELECT COUNT(*) n, MIN(date) start, MAX(date) end,
                      SUM(trading_pl) trading_pl, SUM(cash_yield_pl) cash_yield_pl,
                      SUM(total_pl) total_pl
               FROM book_return_attribution
               WHERE book = ? AND date >= date('now', ?)""",
            (book, f"-{days} days"),
        ).fetchone()
    except sqlite3.Error as exc:
        print(f"WARN: book_return_attribution read skipped: {exc}", file=sys.stderr)
        return None
    if not row or not row["n"]:
        return None
    return {
        "days": row["n"],
        "start": row["start"],
        "end": row["end"],
        "trading_pl": round(row["trading_pl"] or 0.0, 2),
        "cash_yield_pl": round(row["cash_yield_pl"] or 0.0, 2),
        "total_pl": round(row["total_pl"] or 0.0, 2),
    }


def build_objective(cur: sqlite3.Cursor) -> dict:
    """Always-present, measured money scoreboard so the loop is never P&L-blind.

    Pure measurement — never auto-filed as an issue. Makes 'are we beating SPY,
    how deployed are we, is the deployed sleeve earning its keep' first-class in
    the telemetry the PM/health-sweep reads.
    """
    obj: dict = {}
    bench = _latest_benchmarks(cur)
    obj["alpha_by_horizon"] = {
        h: {
            "period": f"{bench[h]['period_start']}..{bench[h]['period_end']}",
            "portfolio_return_pct": bench[h]["portfolio_return_pct"],
            "spy_return_pct": bench[h]["spy_return_pct"],
            "alpha_pct": bench[h]["alpha_pct"],
            "sharpe": bench[h]["sharpe_estimate"],
        }
        for h in OBJECTIVE_HORIZONS
        if h in bench
    }

    cap = _latest_capital_efficiency(cur)
    if cap is not None:
        try:
            loss = json.loads(cap["loss_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            loss = {}
        obj["deployment"] = {
            "as_of": cap["as_of"],
            "equity": cap["equity"],
            "pct_deployed": cap["pct_deployed"],
            "pct_idle": cap["pct_idle"],
            "usd_idle": cap["usd_idle"],
            "dollar_bottlenecks": dict(sorted(loss.items(), key=lambda kv: -(kv[1] or 0))),
        }

    attr = _trailing_book_attribution(cur)
    if attr is not None:
        obj["selection_vs_yield"] = attr

    # One honest, non-alarmist read for humans skimming the report.
    trailing = obj.get("alpha_by_horizon", {}).get("position_1_4w")
    notes = []
    if trailing:
        notes.append(
            f"trailing-month alpha {trailing['alpha_pct']:+.2f}% "
            f"(desk {trailing['portfolio_return_pct']:+.2f}% vs SPY {trailing['spy_return_pct']:+.2f}%)"
        )
    if obj.get("deployment"):
        notes.append(f"{obj['deployment']['pct_idle']:.0f}% idle cash")
    if attr:
        notes.append(f"deployed sleeve trading P&L {attr['trading_pl']:+.0f} vs cash-yield {attr['cash_yield_pl']:+.0f} over {attr['days']}d")
    obj["read"] = "; ".join(notes) if notes else "no benchmark/attribution rows yet"
    return obj


def idle_cash_drag_signal(cur: sqlite3.Cursor) -> dict | None:
    """Code-actionable: idle cash whose dominant cause is idea supply, not risk.

    The fix is origination throughput (more qualified ideas that pass the EXISTING
    gates), never loosening the risk budget — consistent with the protect-first
    posture. Fires only when idle cash is structurally material.
    """
    cap = _latest_capital_efficiency(cur)
    if cap is None:
        return None
    pct_idle = cap["pct_idle"] or 0.0
    usd_idle = cap["usd_idle"] or 0.0
    if pct_idle < IDLE_DRAG_PCT_FLOOR or usd_idle < IDLE_DRAG_USD_FLOOR:
        return None
    try:
        loss = json.loads(cap["loss_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        loss = {}
    attribution = {
        "as_of": cap["as_of"],
        "pct_idle": round(float(pct_idle), 2),
        "usd_idle": round(float(usd_idle), 2),
        "idle_no_qualified_ideas": {
            "expected_loss_usd": round(float(loss.get("idle_no_qualified_ideas") or 0.0), 2),
            "capital_usd": round(float(cap["usd_idle"] or 0.0), 2),
        },
        "gates": {
            "expected_loss_usd": round(float(loss.get("risk_gate_blocked") or 0.0), 2),
            "capital_usd": round(float(cap["usd_blocked"] or 0.0), 2),
        },
        "waiting": {
            "expected_loss_usd": round(float(loss.get("unresolved_predictions_waiting") or 0.0), 2),
            "capital_usd": round(float(cap["usd_waiting"] or 0.0), 2),
        },
        "stale": {
            "expected_loss_usd": round(float(loss.get("stale_thesis_trapped") or 0.0), 2),
            "capital_usd": round(float(cap["usd_stale"] or 0.0), 2),
        },
    }
    ranked = sorted(((k, v or 0.0) for k, v in loss.items()), key=lambda kv: -kv[1])
    top_cause, top_usd = ranked[0] if ranked else ("unknown", 0.0)
    idea_supply_led = top_cause == "idle_no_qualified_ideas"
    return {
        "id": "idle-cash-drag",
        "severity": min(85, int(35 + pct_idle)),
        "summary": (
            f"{pct_idle:.0f}% of equity idle (${usd_idle:,.0f}); "
            f"top dollar bottleneck: {top_cause} ${top_usd:,.0f}"
        ),
        "evidence": [
            f"capital_efficiency_snapshots as_of {cap['as_of']}: pct_deployed={cap['pct_deployed']}, pct_idle={pct_idle}",
            "ranked $ bottlenecks: " + ", ".join(f"{k}=${v:,.0f}" for k, v in ranked),
            "idle attribution from stored DB snapshot: "
            f"no_qualified=${attribution['idle_no_qualified_ideas']['expected_loss_usd']:,.0f}, "
            f"gates=${attribution['gates']['expected_loss_usd']:,.0f}, "
            f"waiting=${attribution['waiting']['expected_loss_usd']:,.0f}, "
            f"stale=${attribution['stale']['expected_loss_usd']:,.0f}",
            "risk-gate blocks are a trivial share — the drag is idea supply, not the risk budget"
            if idea_supply_led else
            f"dominant cause is {top_cause}, not idle idea-supply — root-cause accordingly",
        ],
        "idle_cash_attribution": attribution,
        "suggested_issue": {
            "title": "Reduce idle-cash drag by raising qualified-idea origination throughput",
            "acceptance_criteria": (
                "- Attribute idle cash to its cause (no qualified ideas vs gates vs waiting) with a script, not prose\n"
                "- If idea-supply-limited: raise origination throughput (broaden the daily refresh universe / "
                "signal->hypothesis conversion) so MORE ideas clear the EXISTING gates — do NOT loosen risk caps or the deployment governor\n"
                "- Any sizing/deployment parameter change ships as a rule_proposal, never a direct edit (invariant #4)\n"
                f"- Fresh drag_report.py shows idle_no_qualified_ideas and pct_idle materially reduced (idle < {IDLE_DRAG_PCT_FLOOR:.0f}%)"
            ),
            "assignee": "Developer",
        },
    }


def collect_signals(cur: sqlite3.Cursor) -> list[dict]:
    signals: list[dict] = []

    # --- Blocked-intent classes: each class is a concrete software/gate gap.
    try:
        rows = cur.execute(
            """SELECT id, action, blocked_reason FROM trade_intents
               WHERE state = 'blocked' AND created_at > datetime('now', ?)
               ORDER BY created_at DESC""",
            (f"-{BLOCK_LOOKBACK_DAYS} days",),
        ).fetchall()
        active_classes: dict[str, dict] = {}
        legacy_false_positives: dict[str, dict] = {}
        for row in rows:
            reason = (row["blocked_reason"] or "").strip()
            reevaluated = None
            if reason.startswith("risk:"):
                classified = classify_risk_block({"id": row["id"], "action": row["action"], "blocked_reason": reason, "conn": cur.connection})
            elif reason.startswith("gates_failed:"):
                reevaluated = gate_evaluator.evaluate(cur.connection, row["id"])
                classified = classify_gate_block(row, reevaluated)
            else:
                classified = classify_gate_block(row, reevaluated)
            bucket_set = active_classes if classified["active"] else legacy_false_positives
            bucket = bucket_set.setdefault(
                classified["class_key"],
                {"count": 0, "summary_key": classified["summary_key"], "evidence": []},
            )
            bucket["count"] += 1
            if len(bucket["evidence"]) < 3:
                bucket["evidence"].append(classified["evidence"])

        for key, meta in sorted(active_classes.items(), key=lambda kv: -kv[1]["count"]):
            count = meta["count"]
            if count < 3:
                continue  # noise floor: a class must recur to be a signal
            signals.append({
                "id": f"blocked-{slugify(meta['summary_key'])}"[:64],
                "severity": min(95, 40 + count * 4),
                "summary": f"{count} intents blocked in {BLOCK_LOOKBACK_DAYS}d by the same class: {meta['summary_key']}",
                "evidence": [
                    f"trade_intents state='blocked', class '{meta['summary_key']}', count={count} over {BLOCK_LOOKBACK_DAYS}d",
                    *meta["evidence"],
                ],
                "suggested_issue": {
                    "title": f"Eliminate recurring intent-block class: {meta['summary_key'][:70]}",
                    "acceptance_criteria": (
                        f"- Root-cause the block class '{meta['summary_key']}'\n"
                        "- Fix the responsible stage/gate or document why the block is correct-by-design\n"
                        f"- Fresh drag_report.py run shows this class below 3 occurrences/{BLOCK_LOOKBACK_DAYS}d"
                    ),
                    "assignee": "Developer",
                },
            })
        for key, meta in sorted(legacy_false_positives.items(), key=lambda kv: -kv[1]["count"]):
            count = meta["count"]
            if count < 3:
                continue
            disposition = legacy_artifact_disposition(cur.connection, meta["summary_key"])
            if disposition:
                signals.append({
                    "id": f"historical-artifacts-{slugify(meta['summary_key'])}"[:64],
                    "severity": 5,
                    "summary": (
                        f"{count} blocked intents in {BLOCK_LOOKBACK_DAYS}d are marked historical artifacts "
                        f"and intentionally retained: {meta['summary_key']}"
                    ),
                    "evidence": [
                        (
                            f"historical artifact disposition {disposition['id']} at {disposition['timestamp']}; "
                            f"after_state={disposition.get('after_state') or 'historical_artifact'}"
                        ),
                        (
                            "residual blocked rows are retained for audit/history; Developer does not requeue "
                            "or cancel trade_intents"
                        ),
                        f"disposition rationale: {disposition.get('rationale')}",
                        (
                            f"{count} residual blocked rows still re-evaluate to pass under the current gate stack "
                            "or current portfolio state"
                        ),
                        *meta["evidence"],
                    ],
                    "suggested_issue": None,
                })
                continue
            signals.append({
                "id": f"legacy-blocked-{slugify(meta['summary_key'])}"[:64],
                "severity": min(75, 25 + count * 2),
                "summary": (
                    f"{count} blocked intents in {BLOCK_LOOKBACK_DAYS}d were legacy false positives, "
                    f"not an active recurrence: {meta['summary_key']}"
                ),
                "evidence": [
                    (
                        f"{count} blocked rows still in trade_intents over {BLOCK_LOOKBACK_DAYS}d re-evaluate "
                        "to pass under the current gate stack or current portfolio state"
                    ),
                    *meta["evidence"],
                ],
                "suggested_issue": {
                    "title": f"Clean up legacy blocked intents: {meta['summary_key'][:70]}",
                    "acceptance_criteria": (
                        "- Confirm the current gate stack would pass these rows\n"
                        "- Decide whether to requeue, cancel, or leave them as historical artifacts\n"
                        "- Keep drag_report attribution explicit so legacy false positives do not masquerade as an active gate recurrence"
                    ),
                    "assignee": "Developer",
                },
            })
    except sqlite3.Error as exc:
        print(f"WARN: blocked-intent signal skipped: {exc}", file=sys.stderr)

    # --- Calibration: resolved-prediction Brier vs coin-flip.
    try:
        row = cur.execute(
            """SELECT COUNT(*), AVG(brier_component) FROM predictions
               WHERE resolved_at > datetime('now', ?) AND brier_component IS NOT NULL""",
            (f"-{BRIER_LOOKBACK_DAYS} days",),
        ).fetchone()
        count, brier = int(row[0] or 0), row[1]
        if count >= 20 and brier is not None and brier >= BRIER_COINFLIP - 0.01:
            evidence = [f"predictions resolved {BRIER_LOOKBACK_DAYS}d: n={count}, mean brier_component={brier:.4f}"]
            try:
                report = brier_contributors.build_report(cur.connection, BRIER_LOOKBACK_DAYS)
                selected = report.get("selected_contributor") or {}
                delta = report.get("replay", {}).get("delta_mean_brier")
                if selected:
                    evidence.append(
                        "worst contributor: "
                        f"{selected.get('mechanism')} / {selected.get('regime')} / {selected.get('horizon')} "
                        f"count={selected.get('count')} total_brier={selected.get('total_brier')} "
                        f"mean_brier={selected.get('mean_brier')}"
                    )
                if delta is not None:
                    baseline = report["replay"].get("baseline_class_family_most_observed", {}).get("mean_brier")
                    fixed = report["replay"].get("fixed_root_family_horizon_preferred", {}).get("mean_brier")
                    evidence.append(
                        "post-fix replay delta: "
                        f"before={baseline} after={fixed} delta={delta}"
                    )
                selected_delta = report.get("replay", {}).get("selected_contributor_delta")
                if selected and selected_delta is not None:
                    base_means = report["replay"].get("baseline_class_family_most_observed", {}).get("mechanism_means", {})
                    fix_means = report["replay"].get("fixed_root_family_horizon_preferred", {}).get("mechanism_means", {})
                    mech = selected.get("mechanism")
                    evidence.append(
                        "selected contributor replay delta: "
                        f"mechanism={mech} before={base_means.get(mech)} "
                        f"after={fix_means.get(mech)} delta={selected_delta}"
                    )
                relinked = report.get("replay", {}).get("current_linker_replay") or {}
                relinked_mean = relinked.get("mean_brier")
                relinked_delta = report.get("replay", {}).get("current_linker_delta_vs_actual")
                if relinked_mean is not None and relinked_delta is not None:
                    evidence.append(
                        "TM-263 current-linker replay: "
                        f"before_actual={report.get('actual_mean_brier')} after_replay={relinked_mean} "
                        f"delta={relinked_delta} changed_links={relinked.get('changed_links')}; "
                        "historical resolved rows are not retro-mutated"
                    )
                selected_relinked = report.get("replay", {}).get("selected_contributor_current_linker") or {}
                if selected_relinked:
                    evidence.append(
                        "TM-257 selected bucket under current linker: "
                        f"mechanism={selected_relinked.get('mechanism')} "
                        f"before_count={selected_relinked.get('before_count')} "
                        f"after_count={selected_relinked.get('after_count')} "
                        f"before_mean={selected_relinked.get('before_mean_brier')} "
                        f"after_mean={selected_relinked.get('after_mean_brier')}"
                    )
                next_blocker = report.get("next_blocker") or {}
                if next_blocker:
                    evidence.append(
                        "TM-267 next blocker: "
                        f"{next_blocker.get('kind')} "
                        f"(actual={next_blocker.get('actual_mean_brier')} "
                        f"current_linker_replay={next_blocker.get('current_linker_replay_mean_brier')} "
                        f"changed_links={next_blocker.get('changed_links')})"
                    )
                reason = report.get("selection_reason")
                if reason:
                    evidence.append(f"selection_reason: {reason}")
                evidence.append(
                    "single-regime caveat: all resolved calibration rows in this report are neutral / position_1_4w"
                )
            except Exception as exc:
                evidence.append(f"brier_contributor_breakdown_unavailable: {exc}")
            signals.append({
                "id": "calibration-brier-at-coinflip",
                "severity": min(90, int(55 + (brier - BRIER_COINFLIP) * 400)),
                "summary": f"Mean Brier {brier:.4f} over {count} resolved predictions ({BRIER_LOOKBACK_DAYS}d) — at/near coin-flip (0.25)",
                "evidence": evidence,
                "suggested_issue": {
                    "title": "Calibration: identify and fix the largest Brier contributor",
                    "acceptance_criteria": (
                        "- Deterministic breakdown of Brier by mechanism/regime/horizon (script, not prose)\n"
                        "- One concrete fix targeting the worst contributor (feature, data source, or scoring change)\n"
                        "- Change flows as rule_proposal if it alters trading parameters\n"
                        f"- Fresh drag_report.py shows mean Brier below {BRIER_COINFLIP}"
                    ),
                    "assignee": "Quant",
                },
            })
    except sqlite3.Error as exc:
        print(f"WARN: calibration signal skipped: {exc}", file=sys.stderr)

    # --- Unresolved predictions past horizon: the learning loop starving.
    try:
        stale = count_stale_predictions(cur)
        if stale >= 10:
            signals.append({
                "id": "predictions-unresolved-backlog",
                "severity": min(80, 30 + stale),
                "summary": (
                    f"{stale} predictions older than {STALE_PREDICTION_DAYS}d are past horizon and still unresolved "
                    "— the fast learning loop is starving"
                ),
                "evidence": [
                    f"predictions with NULL realized_outcome older than {STALE_PREDICTION_DAYS}d and past horizon: {stale}"
                ],
                "suggested_issue": {
                    "title": "Resolve or expire the stale-prediction backlog",
                    "acceptance_criteria": (
                        "- Resolver handles all past-horizon predictions (resolve, or expire with an audit row)\n"
                        f"- Fresh drag_report.py shows <10 unresolved predictions older than {STALE_PREDICTION_DAYS}d"
                    ),
                    "assignee": "Developer",
                },
            })
    except sqlite3.Error as exc:
        print(f"WARN: stale-prediction signal skipped: {exc}", file=sys.stderr)

    # --- Idle-cash drag: the desk's #1 measured dollar bottleneck, framed as an
    # origination-throughput code gap (protect-first: never a deploy-more mandate).
    try:
        idle = idle_cash_drag_signal(cur)
        if idle is not None:
            signals.append(idle)
    except sqlite3.Error as exc:
        print(f"WARN: idle-cash-drag signal skipped: {exc}", file=sys.stderr)

    # --- Factor-regime tilt: over-originating a factor the market is punishing (the
    # momentum-unwind blind spot the macro regime layer can't see). Read-only; routes the
    # finding to the backlog. The ACTUATION (factor-aware origination weighting) stays a
    # gated rule_proposal — never an auto edit to sizing/origination.
    try:
        import factor_regime
        fr = factor_regime.snapshot(cur.connection)
        if fr.get("tilted_into_punished_factor"):
            mkt = fr.get("market_leadership", {})
            tilt = fr.get("origination_tilt", {})
            procyc = tilt.get("procyclical_share", 0.0) or 0.0
            signals.append({
                "id": "factor-tilt-into-punished-factor",
                "severity": min(82, int(45 + procyc * 45)),
                "summary": fr.get("read", "origination tilted into a punished factor"),
                "evidence": [
                    f"market leadership={mkt.get('leadership')} "
                    f"(MTUM-VLUE 21d {mkt.get('mom_minus_val_21d')}pp, MTUM-SPY 21d {mkt.get('mom_minus_spy_21d')}pp)",
                    f"origination {procyc*100:.0f}% pro-cyclical (momentum+growth); family_share={tilt.get('family_share')}",
                    "single-regime caveat: all resolved outcomes so far are from ONE adverse regime — "
                    "directional evidence, not conclusive; do not overfit",
                ],
                "suggested_issue": {
                    "title": "Factor-regime awareness: down-weight pro-cyclical origination when momentum is punished",
                    "acceptance_criteria": (
                        "- Consume factor_regime in signal_scan/signals_to_hypotheses as a `factor_fit` weight "
                        "(analogous to the existing regime_fit)\n"
                        "- Down-weight momentum/growth-family conviction when market leadership is value_leading\n"
                        "- BACKTEST it once cross-regime resolved outcomes exist (there is currently NO value-leading "
                        "resolved sample to validate against — a static momentum-avoidance would be single-regime overfitting)\n"
                        "- Ship as a rule_proposal (invariant #4); never a direct sizing/origination edit"
                    ),
                    "assignee": "Quant",
                },
            })
    except Exception as exc:
        print(f"WARN: factor-tilt signal skipped: {exc}", file=sys.stderr)

    # --- Selection alpha: are the desk's actual CLOSED trades beating the market?
    # Now measurable per-trade (attribution.realized_edge_vs_spy_bps, populated once
    # mark_positions + compute_attribution run). The truest "do our picks have edge?".
    try:
        row = cur.execute(
            "SELECT COUNT(*) n, AVG(realized_edge_vs_spy_bps) avg_bps, "
            "SUM(CASE WHEN realized_edge_vs_spy_bps > 0 THEN 1 ELSE 0 END) win "
            "FROM attribution WHERE realized_edge_vs_spy_bps IS NOT NULL "
            "AND closed_at >= datetime('now', '-60 days')"
        ).fetchone()
        n = int(row["n"] or 0)
        avg_bps = row["avg_bps"]
        if n >= 8 and avg_bps is not None and avg_bps <= -50.0:
            win_rate = (int(row["win"] or 0) / n) if n else 0.0
            signals.append({
                "id": "selection-alpha-negative",
                "severity": min(85, int(45 + min(40, -avg_bps / 20))),
                "summary": (
                    f"Closed trades are LOSING to SPY: avg {avg_bps:.0f} bps market-relative "
                    f"over {n} trades (60d), win rate {win_rate:.0%}"
                ),
                "evidence": [
                    f"attribution.realized_edge_vs_spy_bps: n={n}, avg={avg_bps:.0f}bps, winners={int(row['win'] or 0)}",
                    "this is per-trade REALIZED market-relative edge (now measurable via mark_positions "
                    "-> compute_attribution); negative = the deployed sleeve has no selection edge yet",
                    "cross-reference the factor-tilt and calibration signals for the driver; single-regime caveat applies",
                ],
                "suggested_issue": {
                    "title": "Selection alpha negative: attribute per-trade losses to their driver",
                    "acceptance_criteria": (
                        "- Break down realized_edge_vs_spy_bps by mechanism family / factor / regime (script, not prose)\n"
                        "- Identify the worst driver and whether it is regime-specific (factor_regime) or mechanism decay\n"
                        "- Any origination/sizing/mechanism change ships as a rule_proposal, backtested (invariant #4)\n"
                        "- Do NOT overfit to the current single adverse regime"
                    ),
                    "assignee": "Quant",
                },
            })
    except sqlite3.Error as exc:
        print(f"WARN: selection-alpha signal skipped: {exc}", file=sys.stderr)

    # --- Selection alpha BY HORIZON: the 2026-07-28 decomposition found the ONLY
    # positive bucket is the longest horizon (trend_1_3m +129 bps avg) while every
    # sub-month horizon loses (position_1_4w -340, swing/intraday worse). Keep the
    # split visible so the loop measures whether the horizon tilt (rp-horizon-tilt-
    # 20260728) works — or whether the pattern was small-n noise.
    try:
        rows = cur.execute(
            "SELECT horizon, COUNT(*) n, AVG(realized_edge_vs_spy_bps) avg_bps "
            "FROM attribution WHERE realized_edge_vs_spy_bps IS NOT NULL "
            "GROUP BY horizon HAVING n >= 4"
        ).fetchall()
        long_h = [r for r in rows if r["horizon"] == "trend_1_3m"]
        short_h = [r for r in rows if r["horizon"] != "trend_1_3m"]
        if long_h and short_h:
            lg = long_h[0]
            worst = min(short_h, key=lambda r: r["avg_bps"])
            spread = float(lg["avg_bps"]) - float(worst["avg_bps"])
            if spread >= 200.0 and float(worst["avg_bps"]) < 0:
                signals.append({
                    "id": "selection-alpha-horizon-split",
                    "severity": 55,
                    "summary": (
                        f"Horizon split persists: trend_1_3m {lg['avg_bps']:+.0f} bps (n={lg['n']}) vs "
                        f"{worst['horizon']} {worst['avg_bps']:+.0f} bps (n={worst['n']}) — "
                        "the desk only wins at its longest horizon"
                    ),
                    "evidence": [
                        "per-horizon AVG(realized_edge_vs_spy_bps) from attribution (all-time)",
                        "rp-horizon-tilt-20260728 proposes sizing sub-month intents at 0.5x; "
                        "this signal tracks whether the split persists as n grows",
                    ],
                    "suggested_issue": {
                        "title": "Horizon split in realized selection alpha: validate or refute with fresh closes",
                        "acceptance_criteria": (
                            "- Recompute per-horizon realized edge as new trades close (script, not prose)\n"
                            "- If the split persists at n>=25 per bucket, escalate the horizon tilt; if it collapses, retire rp-horizon-tilt\n"
                            "- Any sizing change ships as a rule_proposal (invariant #4)"
                        ),
                        "assignee": "Quant",
                    },
                })
    except sqlite3.Error as exc:
        print(f"WARN: horizon-split signal skipped: {exc}", file=sys.stderr)

    signals.sort(key=lambda s: -s["severity"])
    return signals


def main() -> int:
    try:
        db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        print(json.dumps({"project": "AutoTrade", "error": f"store unavailable: {exc}", "signals": []}))
        return 1
    try:
        cur = db.cursor()
        objective = build_objective(cur)
        signals = collect_signals(cur)
    finally:
        db.close()
    print(json.dumps({
        "project": "AutoTrade",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "objective": objective,
        "signals": signals,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
