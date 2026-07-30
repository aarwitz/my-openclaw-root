#!/usr/bin/env python3
"""Capital efficiency audit with bottleneck ranking by expected dollar impact.

Outputs deterministic JSON for the current desk state:
1) deployed capital
2) risk-gate blocked demand
3) idle cash with no qualified ideas
4) stale-thesis trapped capital
5) unresolved-prediction waiting capital
6) expected return opportunity loss by bottleneck

Usage:
  python3 capital_efficiency_audit.py
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
FEATURE_DB = Path(os.path.expanduser("~/.openclaw/state/features.sqlite"))
# TRADING days per horizon — mirrors worldmodel.HORIZON_DAYS (canonical clock
# shared with predict/grade_outcomes/enforce_horizons).
HORIZON_DAYS = {
    "intraday": 1,
    "swing_1_5d": 3,
    "position_1_4w": 15,
    "trend_1_3m": 45,
    "long_6m_plus": 180,
}
TRADING_TO_CAL = 1.45
GRACE_TD = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pct(x: float, denom: float) -> float:
    if denom <= 0:
        return 0.0
    return max(0.0, min(100.0, (x / denom) * 100.0))


def _q(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> float:
    row = conn.execute(sql, params).fetchone()
    if not row:
        return 0.0
    v = row[0]
    return float(v or 0.0)


def _calc_stale_value(conn: sqlite3.Connection) -> float:
    now = datetime.now(timezone.utc)
    total = 0.0
    rows = conn.execute(
        "SELECT p.current_value, p.qty, p.opened_at, h.time_horizon "
        "FROM positions p LEFT JOIN hypotheses h ON h.id=p.hypothesis_id "
        "WHERE p.state IN ('opening','open','scaling') AND p.qty!=0"
    ).fetchall()
    for cv, qty, opened_at, horizon in rows:
        if not opened_at:
            continue
        try:
            ts = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        except Exception:
            continue
        base_td = HORIZON_DAYS.get(horizon or "", 15)
        max_age = int(math.ceil(base_td * TRADING_TO_CAL + GRACE_TD * TRADING_TO_CAL))
        age = (now - ts).days
        if age > max_age:
            total += abs(float(cv or 0.0)) if cv is not None else abs(float(qty or 0.0))
    return total


def _calc_unresolved_waiting_value(conn: sqlite3.Connection) -> float:
    return _q(
        conn,
        "SELECT COALESCE(SUM(ABS(COALESCE(p.current_value,0))),0) "
        "FROM positions p JOIN hypotheses h ON h.id=p.hypothesis_id "
        "WHERE p.state IN ('opening','open','scaling') AND p.qty!=0 "
        "AND EXISTS (SELECT 1 FROM predictions pr WHERE pr.hypothesis_id=h.id AND pr.resolved_at IS NULL)"
    )


def _prospective_edge_rate(conn: sqlite3.Connection) -> tuple[float, str, int]:
    """Forward edge must come from robust integrated mechanisms, never desk P&L.

    The old implementation multiplied idle cash by all-period realized alpha,
    converting a backward-looking score into a fictitious opportunity cost.
    """
    active_ids = [
        row[0] for row in conn.execute(
            "SELECT id FROM mechanisms WHERE status IN ('active','crowded')"
        )
    ]
    if not active_ids:
        return 0.0, "no_robust_active_mechanisms", 0
    if not FEATURE_DB.exists():
        return 0.0, "robust_artifact_unavailable", len(active_ids)
    feat = sqlite3.connect(f"file:{FEATURE_DB}?mode=ro", uri=True)
    placeholders = ",".join("?" for _ in active_ids)
    rows = feat.execute(
        f"SELECT net_alpha_pct FROM calibrated_mechanisms "
        f"WHERE (id || '__' || horizon) IN ({placeholders}) "
        f"AND bonf_sig=1 AND net_alpha_pct>0",
        active_ids,
    ).fetchall()
    feat.close()
    if not rows:
        return 0.0, "active_mechanisms_lack_robust_forward_artifact", len(active_ids)
    # Median is resistant to one spectacular backtest cell; cap is an
    # additional telemetry-only guard, not a trading assumption.
    rate = min(0.10, statistics.median(float(row[0]) for row in rows) / 100.0)
    return max(0.0, rate), "robust_calibrated_mechanism_median", len(active_ids)


def _classify_idle_cash(residual_cash: float, active_edge_count: int) -> tuple[float, float]:
    """Return (idle_no_qualified_ideas, cash_no_validated_edge)."""
    if active_edge_count <= 0:
        return 0.0, max(0.0, residual_cash)
    return max(0.0, residual_cash), 0.0


def main() -> int:
    conn = sqlite3.connect(DB)

    equity = _q(conn, "SELECT equity FROM book_equity WHERE book='desk' ORDER BY date DESC LIMIT 1")
    cash = _q(conn, "SELECT cash FROM book_equity WHERE book='desk' ORDER BY date DESC LIMIT 1")
    deployed = max(0.0, equity - cash)

    blocked_notional = _q(
        conn,
        "SELECT COALESCE(SUM(ABS(COALESCE(size,0)*COALESCE(entry_price_target,0))),0) "
        "FROM trade_intents WHERE state='blocked' AND action='open' "
        "AND created_at >= datetime('now','-2 day')"
    )

    stale_value = _calc_stale_value(conn)
    unresolved_value = _calc_unresolved_waiting_value(conn)

    # Residual cash after explicit bottlenecks. With no robust forward edge it
    # is a deliberate benchmark position, not an origination failure.
    residual_cash = max(0.0, cash - min(cash, blocked_notional) - min(cash, stale_value))
    edge, edge_source, active_edge_count = _prospective_edge_rate(conn)
    idle_unqualified, cash_no_edge = _classify_idle_cash(
        residual_cash, active_edge_count
    )
    losses = {
        "risk_gate_blocked": blocked_notional * edge,
        "stale_thesis_trapped": stale_value * edge,
        "idle_no_qualified_ideas": idle_unqualified * edge,
        "cash_no_validated_edge": 0.0,
        "unresolved_predictions_waiting": unresolved_value * edge,
    }

    ranked = sorted(
        (
            {
                "bottleneck": k,
                "expected_dollar_impact": round(v, 2),
            }
            for k, v in losses.items()
        ),
        key=lambda x: x["expected_dollar_impact"],
        reverse=True,
    )

    as_of = _now_iso()
    out = {
        "as_of": as_of,
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "deployed": round(deployed, 2),
        "percentages": {
            "deployed": round(_pct(deployed, equity), 2),
            "blocked_by_risk_gates": round(_pct(blocked_notional, equity), 2),
            "idle_no_qualified_ideas": round(_pct(idle_unqualified, equity), 2),
            "cash_no_validated_edge": round(_pct(cash_no_edge, equity), 2),
            "trapped_in_stale_theses": round(_pct(stale_value, equity), 2),
            "waiting_unresolved_predictions": round(_pct(unresolved_value, equity), 2),
        },
        "capital_usd": {
            "blocked_by_risk_gates": round(blocked_notional, 2),
            "idle_no_qualified_ideas": round(idle_unqualified, 2),
            "cash_no_validated_edge": round(cash_no_edge, 2),
            "trapped_in_stale_theses": round(stale_value, 2),
            "waiting_unresolved_predictions": round(unresolved_value, 2),
        },
        "expected_return_loss": {
            "assumed_alpha_rate_period": round(edge, 6),
            "source": edge_source,
            "active_edge_count": active_edge_count,
            "by_bottleneck_usd": {k: round(v, 2) for k, v in losses.items()},
        },
        "ranked_bottlenecks": ranked,
        "notes": [
            "Expected-dollar impact uses only robust integrated forward mechanisms; realized desk alpha is not a forecast.",
            "Cash is classified as cash_no_validated_edge when no robust active mechanism exists.",
            "Percentages are independent bottlenecks and may overlap.",
        ],
    }

    # Persist one history row per run so the alpha metrics panel can trend
    # bottleneck dollars over days/weeks (schema: 0016).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS capital_efficiency_snapshots ("
        "as_of TEXT PRIMARY KEY, equity REAL NOT NULL, cash REAL NOT NULL, "
        "deployed REAL NOT NULL, pct_deployed REAL, pct_blocked REAL, pct_idle REAL, "
        "pct_stale REAL, pct_waiting REAL, usd_blocked REAL, usd_idle REAL, "
        "usd_stale REAL, usd_waiting REAL, edge_rate REAL, loss_json TEXT)"
    )
    p = out["percentages"]
    c = out["capital_usd"]
    conn.execute(
        "INSERT OR REPLACE INTO capital_efficiency_snapshots (as_of, equity, cash, deployed, "
        "pct_deployed, pct_blocked, pct_idle, pct_stale, pct_waiting, "
        "usd_blocked, usd_idle, usd_stale, usd_waiting, edge_rate, loss_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (as_of, out["equity"], out["cash"], out["deployed"],
         p["deployed"], p["blocked_by_risk_gates"],
         round(_pct(residual_cash, equity), 2),
         p["trapped_in_stale_theses"], p["waiting_unresolved_predictions"],
         c["blocked_by_risk_gates"], round(residual_cash, 2),
         c["trapped_in_stale_theses"], c["waiting_unresolved_predictions"],
         out["expected_return_loss"]["assumed_alpha_rate_period"],
         json.dumps(out["expected_return_loss"]["by_bottleneck_usd"])),
    )
    conn.commit()

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
