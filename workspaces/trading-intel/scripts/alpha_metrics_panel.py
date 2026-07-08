#!/usr/bin/env python3
"""Alpha metrics panel — one deterministic dashboard for the closed-loop desk.

Sections:
  1. Book        — equity, cash, deployment, day attribution (trading vs cash yield)
  2. Alpha       — per-horizon vs SPY + trading-vs-yield split of inception alpha
  3. Calibration — prediction cohorts, first-maturity countdown, resolved counts
  4. Lifecycle   — hypothesis states, exit-lane throughput (stops/horizons/swaps)
  5. Bottlenecks — latest capital-efficiency ranking + trend vs N days ago
  6. ML trust    — advisory ranker citation/agreement/outperformance ledger

Read-only against trading-intel.sqlite. Human-first text output; --json for machines.

  python3 alpha_metrics_panel.py [--json] [--trend-days 7]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
HORIZON_DAYS_TD = {"intraday": 1, "swing_1_5d": 3, "position_1_4w": 15,
                   "trend_1_3m": 45, "long_6m_plus": 180}
TRADING_TO_CAL = 1.45


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _scalar(conn, sql, params=(), default=None):
    row = conn.execute(sql, params).fetchone()
    return default if row is None or row[0] is None else row[0]


def section_book(conn) -> dict:
    eq = _scalar(conn, "SELECT equity FROM book_equity WHERE book='desk' ORDER BY date DESC LIMIT 1", default=0.0)
    cash = _scalar(conn, "SELECT cash FROM book_equity WHERE book='desk' ORDER BY date DESC LIMIT 1", default=0.0)
    attr = conn.execute(
        "SELECT date, trading_pl, cash_yield_pl, total_pl, trading_return_pct, "
        "cash_yield_return_pct, total_return_pct FROM book_return_attribution "
        "WHERE book='desk' ORDER BY date DESC LIMIT 1").fetchone()
    cum_yield = _scalar(conn, "SELECT COALESCE(SUM(credit),0) FROM sim_cash_yield_events WHERE book='desk'", default=0.0)
    return {
        "equity": round(float(eq), 2),
        "cash": round(float(cash), 2),
        "deployed_pct": round((1 - float(cash) / float(eq)) * 100, 2) if eq else None,
        "today": None if attr is None else {
            "date": attr[0],
            "trading_pl": round(float(attr[1]), 2),
            "cash_yield_pl": round(float(attr[2]), 2),
            "total_pl": round(float(attr[3]), 2),
        },
        "cumulative_cash_yield_pl": round(float(cum_yield), 2),
    }


def section_alpha(conn) -> dict:
    rows = conn.execute(
        "SELECT horizon, portfolio_return_pct, spy_return_pct, alpha_pct, captured_at "
        "FROM benchmarks WHERE captured_at = (SELECT MAX(captured_at) FROM benchmarks) "
        "ORDER BY CASE horizon WHEN 'intraday' THEN 0 WHEN 'swing_1_5d' THEN 1 "
        "WHEN 'position_1_4w' THEN 2 WHEN 'trend_1_3m' THEN 3 ELSE 4 END").fetchall()
    horizons = [{"horizon": r[0], "portfolio_pct": r[1], "spy_pct": r[2], "alpha_pct": r[3]} for r in rows]
    inception_alpha = next((h["alpha_pct"] for h in horizons if h["horizon"] in ("trend_1_3m", "long_6m_plus")), None)
    start_eq = _scalar(conn, "SELECT equity FROM book_equity WHERE book='desk' ORDER BY date ASC LIMIT 1")
    cum_yield = _scalar(conn, "SELECT COALESCE(SUM(credit),0) FROM sim_cash_yield_events WHERE book='desk'", default=0.0)
    yield_pct = (float(cum_yield) / float(start_eq) * 100) if start_eq else None
    return {
        "as_of": rows[0][4] if rows else None,
        "horizons": horizons,
        "alpha_split": {
            "total_alpha_pct_inception": inception_alpha,
            "cash_yield_contribution_pct": None if yield_pct is None else round(yield_pct, 4),
            "trading_alpha_pct_inception": None if (inception_alpha is None or yield_pct is None)
            else round(float(inception_alpha) - yield_pct, 4),
        },
    }


def section_calibration(conn) -> dict:
    open_n = int(_scalar(conn, "SELECT COUNT(*) FROM predictions WHERE resolved_at IS NULL", default=0))
    resolved_n = int(_scalar(conn, "SELECT COUNT(*) FROM predictions WHERE resolved_at IS NOT NULL", default=0))
    brier = _scalar(conn, "SELECT AVG(brier_component) FROM predictions WHERE brier_component IS NOT NULL")
    # earliest maturity among open predictions, on the canonical wm clock
    now = _now()
    soonest = None
    for predicted_at, horizon in conn.execute(
            "SELECT predicted_at, horizon FROM predictions WHERE resolved_at IS NULL"):
        try:
            t0 = datetime.fromisoformat(str(predicted_at).replace("Z", "+00:00"))
        except Exception:
            continue
        mature_days = int(HORIZON_DAYS_TD.get(horizon, 15) * TRADING_TO_CAL)
        days_left = mature_days - (now - t0).days
        if soonest is None or days_left < soonest:
            soonest = days_left
    resolved_7d = int(_scalar(
        conn, "SELECT COUNT(*) FROM predictions WHERE resolved_at >= datetime('now','-7 day')", default=0))
    return {
        "open_predictions": open_n,
        "resolved_lifetime": resolved_n,
        "resolved_last_7d": resolved_7d,
        "running_brier": None if brier is None else round(float(brier), 4),
        "days_to_next_maturity": soonest,
    }


def section_lifecycle(conn) -> dict:
    states = {r[0]: r[1] for r in conn.execute("SELECT state, COUNT(*) FROM hypotheses GROUP BY state")}
    lanes = {}
    for lane, trig in (("stop_exits", "stop_rule_enforcer_v1"),
                       ("soft_stop_trims", "stop_rule_soft_enforcer_v1"),
                       ("horizon_exits", "horizon_enforcer_v1"),
                       ("swap_exits", "swap_rotation")):
        row = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN state='filled' THEN 1 ELSE 0 END) "
            "FROM trade_intents WHERE triggered_by=?", (trig,)).fetchone()
        lanes[lane] = {"authored": int(row[0] or 0), "filled": int(row[1] or 0)}
    return {"hypothesis_states": states, "exit_lanes": lanes}


def section_bottlenecks(conn, trend_days: int) -> dict:
    try:
        latest = conn.execute(
            "SELECT as_of, pct_deployed, usd_blocked, usd_idle, usd_stale, usd_waiting, loss_json "
            "FROM capital_efficiency_snapshots ORDER BY as_of DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return {"available": False, "note": "capital_efficiency_snapshots missing — run capital_efficiency_audit.py"}
    if latest is None:
        return {"available": False}
    prior = conn.execute(
        "SELECT as_of, pct_deployed, usd_blocked, usd_idle, usd_stale, usd_waiting "
        "FROM capital_efficiency_snapshots WHERE as_of <= datetime('now', ?) "
        "ORDER BY as_of DESC LIMIT 1", (f"-{trend_days} day",)).fetchone()
    losses = json.loads(latest[6] or "{}")
    ranked = sorted(losses.items(), key=lambda kv: -(kv[1] or 0))
    out = {
        "available": True,
        "as_of": latest[0],
        "pct_deployed": latest[1],
        "capital_usd": {"blocked": latest[2], "idle": latest[3], "stale": latest[4], "waiting": latest[5]},
        "ranked_expected_loss_usd": [{"bottleneck": k, "usd": round(v, 2)} for k, v in ranked],
    }
    if prior and prior[0] != latest[0]:
        out["trend_vs"] = prior[0]
        out["delta"] = {
            "pct_deployed": round((latest[1] or 0) - (prior[1] or 0), 2),
            "usd_blocked": round((latest[2] or 0) - (prior[2] or 0), 2),
            "usd_idle": round((latest[3] or 0) - (prior[3] or 0), 2),
            "usd_stale": round((latest[4] or 0) - (prior[4] or 0), 2),
            "usd_waiting": round((latest[5] or 0) - (prior[5] or 0), 2),
        }
    return out


def section_ml_trust(conn) -> dict:
    try:
        row = conn.execute(
            "SELECT COUNT(*), SUM(cited_ml), "
            "SUM(CASE WHEN cited_ml=1 AND agreement='agree' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN cited_ml=1 AND agreement='disagree' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN cited_ml=1 AND resolved_outperformed IS NOT NULL THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN cited_ml=1 AND resolved_outperformed=1 THEN 1 ELSE 0 END) "
            "FROM ml_evidence_tracking").fetchone()
    except sqlite3.OperationalError:
        return {"available": False}
    tracked, cited, agree, disagree, resolved, outperf = [int(x or 0) for x in row]
    model_eq = _scalar(conn, "SELECT equity FROM book_equity WHERE book='model' ORDER BY date DESC LIMIT 1")
    model_start = _scalar(conn, "SELECT starting_cash FROM sim_accounts WHERE book='model'")
    model_ret = None
    if model_eq and model_start:
        model_ret = round((float(model_eq) / float(model_start) - 1) * 100, 2)
    return {
        "available": True,
        "hypotheses_tracked": tracked,
        "cited_ml": cited,
        "agree": agree,
        "disagree": disagree,
        "cited_resolved": resolved,
        "cited_outperformed": outperf,
        "cited_outperformed_rate": None if resolved == 0 else round(outperf / resolved, 4),
        "model_book_return_pct": model_ret,
        "quarantine_note": "ranker stays advisory until model book shows positive 30-day alpha",
    }


def render_text(panel: dict) -> str:
    b, a, c, lc, bn, ml = (panel[k] for k in
                           ("book", "alpha", "calibration", "lifecycle", "bottlenecks", "ml_trust"))
    L = []
    L.append(f"ALPHA METRICS PANEL — {panel['as_of']}")
    L.append("")
    t = b.get("today") or {}
    L.append(f"BOOK   equity ${b['equity']:,.0f}  cash ${b['cash']:,.0f}  deployed {b['deployed_pct']}%")
    if t:
        L.append(f"       today: trading {t['trading_pl']:+.2f}  cash-yield {t['cash_yield_pl']:+.2f}  total {t['total_pl']:+.2f}"
                 f"   (cum yield ${b['cumulative_cash_yield_pl']:.2f})")
    L.append("")
    split = a["alpha_split"]
    L.append("ALPHA  vs SPY: " + "  ".join(
        f"{h['horizon']}={h['alpha_pct']:+.2f}%" for h in a["horizons"]))
    if split["total_alpha_pct_inception"] is not None:
        L.append(f"       inception split: total {split['total_alpha_pct_inception']:+.2f}%"
                 f" = trading {split['trading_alpha_pct_inception']:+.2f}%"
                 f" + cash-yield {split['cash_yield_contribution_pct']:+.2f}%")
    L.append("")
    L.append(f"CALIB  {c['open_predictions']} open / {c['resolved_lifetime']} resolved"
             f" ({c['resolved_last_7d']} in 7d)  brier={c['running_brier']}"
             f"  next maturity in {c['days_to_next_maturity']}d")
    L.append("")
    hs = lc["hypothesis_states"]
    L.append("LOOP   hypotheses: " + "  ".join(f"{k}={v}" for k, v in sorted(hs.items())))
    lanes = lc["exit_lanes"]
    L.append("       exit lanes: " + "  ".join(
        f"{k}={v['authored']}auth/{v['filled']}filled" for k, v in lanes.items()))
    L.append("")
    if bn.get("available"):
        L.append(f"BOTTLENECKS (as of {bn['as_of']}, deployed {bn['pct_deployed']}%)")
        for r in bn["ranked_expected_loss_usd"]:
            L.append(f"       {r['bottleneck']:32s} ${r['usd']:>9,.2f} expected loss")
        if "delta" in bn:
            d = bn["delta"]
            L.append(f"       trend vs {bn['trend_vs']}: deployed {d['pct_deployed']:+.2f}pp"
                     f"  idle {d['usd_idle']:+,.0f}  stale {d['usd_stale']:+,.0f}"
                     f"  blocked {d['usd_blocked']:+,.0f}")
    L.append("")
    if ml.get("available"):
        L.append(f"ML     cited={ml['cited_ml']}/{ml['hypotheses_tracked']}"
                 f"  agree/disagree={ml['agree']}/{ml['disagree']}"
                 f"  cited-resolved outperf rate={ml['cited_outperformed_rate']}"
                 f"  model book={ml['model_book_return_pct']}%")
        L.append(f"       {ml['quarantine_note']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--trend-days", type=int, default=7)
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    panel = {
        "as_of": _iso(_now()),
        "book": section_book(conn),
        "alpha": section_alpha(conn),
        "calibration": section_calibration(conn),
        "lifecycle": section_lifecycle(conn),
        "bottlenecks": section_bottlenecks(conn, args.trend_days),
        "ml_trust": section_ml_trust(conn),
    }
    conn.close()
    print(json.dumps(panel, indent=2) if args.json else render_text(panel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
