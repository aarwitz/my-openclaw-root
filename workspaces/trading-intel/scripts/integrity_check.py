#!/usr/bin/env python3
"""integrity_check.py — fail-fast verifier: is the desk actually closing the loop?

A month of silent holes (risk_model on a frozen book, positions.pnl NULL forever,
compute_attribution crashing nightly, the sizing signal anti-predictive) all looked
GREEN because nothing asserted the system's real job. Unit tests can't catch those —
each component "works"; the failures live in the SEAMS (loop not closed), the DATA
(silently wrong/NULL), and the OUTCOME (no edge). This asserts all three, read-only,
every run, and is LOUD when red.

Two verdicts, deliberately separate:
  * INTEGRITY — data-reality + loop-closure. RED = the machine is broken, fix fast.
    Exit code is non-zero on any integrity failure so a cron/health-sweep screams.
  * EDGE — does the strategy actually predict returns? RED here is an honest "no alpha
    yet", NOT a bug — but it must never be hidden behind "we're being patient".

Add a check when you find a new class of silent hole; that is the whole point.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone

DB_PATH = "/home/aaron/.openclaw/state/trading-intel.sqlite"
OPEN_STATES = ("opening", "open", "scaling", "trimming", "closing")

# (table, column, where, max_null_fraction, label) — a column the system CLAIMS to have.
NULL_HOLE_CHECKS = [
    ("positions", "unrealized_pnl_pct", "state != 'closed'", 0.5, "open positions have no marked P&L"),
    ("positions", "regime_at_first_open", "state != 'closed'", 0.9, "positions never record their entry regime"),
    ("attribution", "realized_edge_vs_spy_bps", "1=1", 0.5, "closed trades have no realized edge vs SPY"),
    ("predictions", "realized_excess_pct", "resolved_at IS NOT NULL", 0.5, "resolved predictions have no graded outcome"),
    ("portfolio_snapshots", "cash", "1=1", 0.5, "portfolio snapshots have no cash figure"),
]

# (table, ts_column, max_age_hours, label) — is the learning/telemetry output fresh?
FRESHNESS_CHECKS = [
    ("benchmarks", "captured_at", 30, "SPY scoreboard"),
    ("capital_efficiency_snapshots", "as_of", 30, "capital-efficiency telemetry"),
    ("portfolio_risk", "as_of", 30, "portfolio risk snapshot"),
]


def _rows(conn, q, *a):
    return conn.execute(q, a).fetchall()


def data_reality(conn) -> list[dict]:
    out = []
    for tbl, col, where, max_frac, label in NULL_HOLE_CHECKS:
        try:
            r = conn.execute(f"SELECT COUNT(*) n, SUM({col} IS NULL) nulls FROM {tbl} WHERE {where}").fetchone()
        except sqlite3.Error as e:
            out.append({"family": "data", "id": f"nullhole:{tbl}.{col}", "status": "RED",
                        "detail": f"cannot read {tbl}.{col}: {e}"})
            continue
        n = r["n"] or 0
        if n == 0:
            continue
        frac = (r["nulls"] or 0) / n
        out.append({
            "family": "data", "id": f"nullhole:{tbl}.{col}",
            "status": "RED" if frac > max_frac else "OK",
            "detail": f"{label}: {r['nulls']}/{n} NULL ({frac:.0%})",
        })

    # Consistency: does the RISK MODEL see the same book the DB holds? (frozen-source detector)
    try:
        db_n = conn.execute(
            f"SELECT COUNT(DISTINCT UPPER(ticker)) FROM positions WHERE state IN ({','.join('?'*len(OPEN_STATES))})",
            OPEN_STATES,
        ).fetchone()[0]
        pr = conn.execute("SELECT n_positions FROM portfolio_risk ORDER BY as_of DESC LIMIT 1").fetchone()
        pr_n = pr["n_positions"] if pr else None
        if pr_n is not None and db_n:
            drift = abs(pr_n - db_n) / db_n
            out.append({
                "family": "data", "id": "consistency:risk_model_book",
                "status": "RED" if drift > 0.25 else "OK",
                "detail": f"portfolio_risk n_positions={pr_n} vs live book={db_n} (drift {drift:.0%}) "
                          "— a big gap means the risk view is on a stale/frozen source",
            })
    except sqlite3.Error:
        pass
    return out


def loop_closure(conn) -> list[dict]:
    out = []
    for tbl, tsc, max_h, label in FRESHNESS_CHECKS:
        try:
            last = conn.execute(f"SELECT MAX({tsc}) FROM {tbl}").fetchone()[0]
        except sqlite3.Error:
            last = None
        if not last:
            out.append({"family": "loop", "id": f"fresh:{tbl}", "status": "RED", "detail": f"{label}: never written"})
            continue
        age_h = conn.execute("SELECT (julianday('now') - julianday(?)) * 24", (last,)).fetchone()[0]
        out.append({"family": "loop", "id": f"fresh:{tbl}", "status": "RED" if age_h > max_h else "OK",
                    "detail": f"{label}: last update {age_h:.1f}h ago"})

    # Contract: closed positions must produce an attribution row WITH a realized edge (pnl->attribution loop).
    try:
        gap = conn.execute(
            "SELECT COUNT(*) FROM positions p WHERE p.state='closed' AND p.closed_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM attribution a WHERE a.position_id=p.id "
            "AND a.realized_edge_vs_spy_bps IS NOT NULL)"
        ).fetchone()[0]
        closed = conn.execute("SELECT COUNT(*) FROM positions WHERE state='closed' AND closed_at IS NOT NULL").fetchone()[0]
        out.append({"family": "loop", "id": "contract:closed->attribution",
                    "status": "RED" if (closed and gap / closed > 0.3) else "OK",
                    "detail": f"{gap}/{closed} closed trades have no realized-edge attribution "
                              "— the per-trade P&L loop is open"})
    except sqlite3.Error:
        pass

    # Learning loop starving: predictions long past horizon still unresolved.
    try:
        stale = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE resolved_at IS NULL AND predicted_at < datetime('now','-30 days')"
        ).fetchone()[0]
        out.append({"family": "loop", "id": "learn:unresolved_backlog",
                    "status": "RED" if stale > 20 else ("WARN" if stale > 5 else "OK"),
                    "detail": f"{stale} predictions >30d old still unresolved"})
    except sqlite3.Error:
        pass
    return out


def edge(conn) -> list[dict]:
    """Honest 'do we have alpha?' — RED here is truth, not a bug."""
    out = []
    rows = _rows(conn, "SELECT p_correct pc, realized_excess_pct rex FROM predictions "
                       "WHERE resolved_at IS NOT NULL AND p_correct IS NOT NULL AND realized_excess_pct IS NOT NULL")
    if len(rows) >= 20:
        a = [r["pc"] for r in rows]
        b = [r["rex"] for r in rows]
        ma, mb = statistics.mean(a), statistics.mean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a) ** 0.5
        dbb = sum((y - mb) ** 2 for y in b) ** 0.5
        corr = num / (da * dbb) if da and dbb else 0.0
        out.append({"family": "edge", "id": "edge:conviction_predicts",
                    "status": "RED" if corr <= 0.05 else "OK",
                    "detail": f"corr(p_correct, realized_excess) = {corr:+.2f} over n={len(rows)} "
                              "— the sizing conviction signal does not predict returns"})
    row = conn.execute(
        "SELECT COUNT(*) n, AVG(realized_edge_vs_spy_bps) avg FROM attribution "
        "WHERE realized_edge_vs_spy_bps IS NOT NULL AND closed_at >= datetime('now','-90 days')"
    ).fetchone()
    if (row["n"] or 0) >= 8:
        out.append({"family": "edge", "id": "edge:selection_alpha",
                    "status": "RED" if (row["avg"] or 0) <= 0 else "OK",
                    "detail": f"closed-trade realized edge avg {row['avg']:.0f} bps vs SPY over {row['n']} trades"})
    return out


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = data_reality(conn) + loop_closure(conn)
        edge_checks = edge(conn)
    finally:
        conn.close()
    integ_red = [c for c in integrity if c["status"] == "RED"]
    edge_red = [c for c in edge_checks if c["status"] == "RED"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "integrity": {"status": "BROKEN" if integ_red else "OK",
                      "red": len(integ_red), "checks": integrity},
        "edge": {"status": "NO_EDGE" if edge_red else "OK",
                 "note": "RED = honest 'no alpha yet', not a bug — but never hide it behind 'be patient'",
                 "checks": edge_checks},
        "headline": (
            f"INTEGRITY {'BROKEN' if integ_red else 'OK'} ({len(integ_red)} broken loops/data holes); "
            f"EDGE {'NONE' if edge_red else 'present'}"
        ),
    }
    print(json.dumps(report, indent=1))
    # Non-zero ONLY on integrity failure — a broken machine must fail the check.
    # No-edge is reported loudly but does not itself fail (it's a strategy verdict, not a bug).
    return 1 if integ_red else 0


if __name__ == "__main__":
    raise SystemExit(main())
