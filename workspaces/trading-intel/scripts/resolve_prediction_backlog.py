#!/usr/bin/env python3
"""Resolve or expire matured predictions deterministically.

This script closes the gap between prediction issuance and the slower
hypothesis-grading loop. It resolves only past-horizon predictions; future
predictions remain untouched even if they are older than 21 days.

Resolution paths:
  * realized outcome available from name-vs-SPY returns -> correct/incorrect
  * realized excess inside the dead-band -> inconclusive
  * missing ticker/price window after maturity -> expire with an explicit audit

Safe to re-run: only unresolved predictions are scanned, and each prediction is
committed atomically with its audit/observation side effects.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/quant/scripts"))))
import feature_store as fs  # noqa: E402
import predict  # noqa: E402
import worldmodel as wm  # noqa: E402

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
EXPERIMENT_DEFAULT = "world_model_v1"
EXCESS_DEADBAND_PCT = 0.5


@dataclass
class WindowResult:
    entry_date: str
    exit_date: str
    return_pct: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _resolved_at_for(exit_date: str) -> str:
    return f"{exit_date}T00:00:00Z"


def _mechanism_links(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    links: list[dict] = []
    for item in data:
        if isinstance(item, str):
            links.append({"id": item, "align": 1})
        elif isinstance(item, dict) and item.get("id"):
            align = int(item.get("align", 1) or 1)
            links.append({"id": item["id"], "align": align})
    return links


def _window_return(prices: list[dict], entry_iso: str, horizon_days: int) -> WindowResult | None:
    dates = [bar["t"] for bar in prices]
    closes = {bar["t"]: bar["c"] for bar in prices}
    index = 0
    while index < len(dates) and dates[index] < entry_iso:
        index += 1
    if index >= len(dates):
        return None
    exit_index = index + horizon_days
    if exit_index >= len(dates):
        return None
    start = closes.get(dates[index])
    end = closes.get(dates[exit_index])
    if not start or end is None:
        return None
    return WindowResult(
        entry_date=dates[index],
        exit_date=dates[exit_index],
        return_pct=(end / start - 1.0) * 100.0,
    )


def _load_prices(symbol: str) -> list[dict]:
    return fs._prices(symbol, 4000)


def _insert_audit(
    conn: sqlite3.Connection,
    *,
    prediction_id: str,
    action: str,
    after_state: str,
    rationale: str,
    experiment_id: str,
    actor: str,
) -> None:
    audit_id = f"AUDIT-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{prediction_id[:18]}-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, before_state, after_state, rationale_concise, experiment_id) "
        "VALUES (?, ?, ?, 'prediction', ?, ?, 'unresolved', ?, ?, ?)",
        (audit_id, _now_iso(), actor, prediction_id, action, after_state, rationale[:500], experiment_id),
    )


def _emit_mechanism_observations(
    conn: sqlite3.Connection,
    *,
    prediction_id: str,
    outcome: str,
    observed_at: str,
    mechanism_ids_json: str | None,
    regime_at_prediction: str | None,
    experiment_id: str,
) -> int:
    if outcome not in {"correct", "incorrect"}:
        return 0
    correct = outcome == "correct"
    count = 0
    for link in _mechanism_links(mechanism_ids_json):
        align = int(link.get("align", 1) or 1)
        mech_correct = correct if align >= 0 else (not correct)
        obs_outcome = "hit" if mech_correct else "miss"
        conn.execute(
            "INSERT INTO mechanism_observations (id, mechanism_id, observed_at, source_type, source_id, outcome, weight, regime_at_obs, notes, experiment_id) "
            "VALUES (?, ?, ?, 'prediction', ?, ?, 1.0, ?, ?, ?)",
            (
                "mobs-" + uuid.uuid4().hex[:20],
                link["id"],
                observed_at,
                prediction_id,
                obs_outcome,
                regime_at_prediction,
                f"from prediction {prediction_id} (align={align}, thesis={outcome})",
                experiment_id,
            ),
        )
        count += 1
    return count


def resolve_prediction_backlog(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    actor: str = "developer",
    now: datetime | None = None,
    price_loader: Callable[[str], list[dict]] = _load_prices,
) -> dict:
    now = now or _utc_now()
    rows = conn.execute(
        "SELECT p.id, p.hypothesis_id, p.predicted_at, p.horizon, p.p_correct, p.mechanism_ids_json, "
        "p.regime_at_prediction, p.experiment_id, h.tickers, h.thesis_summary "
        "FROM predictions p JOIN hypotheses h ON h.id = p.hypothesis_id "
        "WHERE p.resolved_at IS NULL AND p.realized_outcome IS NULL "
        "ORDER BY p.predicted_at ASC, p.id ASC"
    ).fetchall()

    summary = {
        "scanned": len(rows),
        "matured": 0,
        "resolved": 0,
        "expired": 0,
        "inconclusive": 0,
        "skipped_future": 0,
        "details": [],
    }
    price_cache: dict[str, list[dict]] = {}

    spy_prices = price_loader("SPY")

    for row in rows:
        horizon_days = wm.HORIZON_DAYS.get(row["horizon"], 15)
        spy_window = _window_return(spy_prices, row["predicted_at"][:10], horizon_days)
        if spy_window is None:
            summary["skipped_future"] += 1
            continue
        summary["matured"] += 1

        experiment_id = row["experiment_id"] or EXPERIMENT_DEFAULT
        ticker = predict._first_ticker(row["tickers"])
        if not ticker:
            detail = {"prediction_id": row["id"], "status": "expired", "reason": "missing_ticker"}
            summary["details"].append(detail)
            summary["expired"] += 1
            if not dry_run:
                with conn:
                    conn.execute(
                        "UPDATE predictions SET realized_outcome='inconclusive', resolved_at=? WHERE id=?",
                        (_now_iso(), row["id"]),
                    )
                    _insert_audit(
                        conn,
                        prediction_id=row["id"],
                        action="expire_prediction",
                        after_state="expired_missing_ticker",
                        rationale="past horizon with no ticker; prediction expired as inconclusive",
                        experiment_id=experiment_id,
                        actor=actor,
                    )
            continue

        if ticker not in price_cache:
            try:
                price_cache[ticker] = price_loader(ticker)
            except Exception:
                price_cache[ticker] = []

        ticker_window = _window_return(price_cache[ticker], row["predicted_at"][:10], horizon_days)
        if ticker_window is None or spy_window is None:
            detail = {"prediction_id": row["id"], "status": "expired", "reason": "missing_price_window", "ticker": ticker}
            summary["details"].append(detail)
            summary["expired"] += 1
            if not dry_run:
                with conn:
                    conn.execute(
                        "UPDATE predictions SET realized_outcome='inconclusive', resolved_at=? WHERE id=?",
                        (_now_iso(), row["id"]),
                    )
                    _insert_audit(
                        conn,
                        prediction_id=row["id"],
                        action="expire_prediction",
                        after_state="expired_missing_price_window",
                        rationale=f"past horizon but price window unavailable for {ticker} or SPY; prediction expired as inconclusive",
                        experiment_id=experiment_id,
                        actor=actor,
                    )
            continue

        direction = predict.thesis_direction(row["thesis_summary"] or "")
        excess_pct = round(ticker_window.return_pct - spy_window.return_pct, 3)
        realized_return_pct = round(ticker_window.return_pct, 3)
        resolved_at = _resolved_at_for(ticker_window.exit_date)

        if abs(excess_pct) <= EXCESS_DEADBAND_PCT:
            outcome = "inconclusive"
            brier = None
        else:
            correct = excess_pct < 0 if direction == "short" else excess_pct > 0
            outcome = "correct" if correct else "incorrect"
            bit = 1.0 if correct else 0.0
            brier = round((float(row["p_correct"]) - bit) ** 2, 6)

        observations = 0
        if not dry_run:
            with conn:
                observations = _emit_mechanism_observations(
                    conn,
                    prediction_id=row["id"],
                    outcome=outcome,
                    observed_at=resolved_at,
                    mechanism_ids_json=row["mechanism_ids_json"],
                    regime_at_prediction=row["regime_at_prediction"],
                    experiment_id=experiment_id,
                )
                conn.execute(
                    "UPDATE predictions SET realized_outcome=?, realized_return_pct=?, realized_excess_pct=?, brier_component=?, resolved_at=? WHERE id=?",
                    (outcome, realized_return_pct, excess_pct, brier, resolved_at, row["id"]),
                )
                _insert_audit(
                    conn,
                    prediction_id=row["id"],
                    action="resolve_prediction",
                    after_state=outcome,
                    rationale=(
                        f"ticker={ticker} ret={realized_return_pct:+.3f}% spy={spy_window.return_pct:+.3f}% "
                        f"excess={excess_pct:+.3f}% dir={direction} brier={brier} obs+={observations}"
                    ),
                    experiment_id=experiment_id,
                    actor=actor,
                )
        detail = {
            "prediction_id": row["id"],
            "ticker": ticker,
            "status": outcome,
            "realized_return_pct": realized_return_pct,
            "realized_excess_pct": excess_pct,
            "resolved_at": resolved_at,
        }
        summary["details"].append(detail)
        if outcome == "inconclusive":
            summary["inconclusive"] += 1
        else:
            summary["resolved"] += 1

    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--actor", default="developer")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        result = resolve_prediction_backlog(conn, dry_run=args.dry_run, actor=args.actor)
    finally:
        conn.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
