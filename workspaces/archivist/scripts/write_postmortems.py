#!/usr/bin/env python3
"""Archivist · write_postmortems.py — universal postmortem writer (D56).

Learning-loop keystone repair: hypotheses were being resolved (stop-outs,
horizon expiries, grades) but NOTHING wrote `postmortems` rows, so
extract_patterns.py had zero input and the desk never converted losses into
recurring-pattern knowledge. This script guarantees every resolved hypothesis
gets exactly one structured postmortem.

Theme is derived deterministically from the resolution lane:
  filled stop exit         → stop_whipsaw_or_tight_stop
  filled horizon exit      → horizon_expiry_no_confirmation
  filled swap exit         → rotated_out
  resolved_state wrong     → thesis_wrong
  correct_wrong_reasons    → right_for_wrong_reasons
  correct_right_reasons    → thesis_validated

Attribution (realized edge vs SPY) and exit-quality (post-exit rebound) rows
are folded in when present, so each postmortem carries the numbers that tell
us WHY it was good or bad. Idempotent; safe to re-run.

  python3 write_postmortems.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _theme(conn: sqlite3.Connection, hid: str, resolved_state: str | None) -> tuple[str, str]:
    """(theme, lane) for a resolved hypothesis."""
    lane_row = conn.execute(
        "SELECT triggered_by FROM trade_intents WHERE hypothesis_id=? "
        "AND action IN ('exit','trim') AND state='filled' "
        "ORDER BY created_at DESC LIMIT 1", (hid,)).fetchone()
    lane = (lane_row[0] if lane_row else "") or ""
    if lane.startswith("stop_rule"):
        return "stop_whipsaw_or_tight_stop", lane
    if lane.startswith("horizon"):
        return "horizon_expiry_no_confirmation", lane
    if lane.startswith("swap"):
        return "rotated_out", lane
    rs = (resolved_state or "").lower()
    if rs == "correct_right_reasons":
        return "thesis_validated", lane
    if rs == "correct_wrong_reasons":
        return "right_for_wrong_reasons", lane
    return "thesis_wrong", lane


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row

    missing = conn.execute(
        "SELECT h.id, h.tickers, h.resolved_state, h.resolved_at, h.archivist_grade, "
        "h.thesis_summary FROM hypotheses h WHERE h.state='resolved' "
        "AND h.id NOT IN (SELECT hypothesis_id FROM postmortems)").fetchall()

    written = []
    for h in missing:
        hid = h["id"]
        theme, lane = _theme(conn, hid, h["resolved_state"])
        try:
            tickers = json.loads(h["tickers"] or "[]")
        except json.JSONDecodeError:
            tickers = [h["tickers"]] if h["tickers"] else []
        ticker = (tickers[0] if tickers else "?")

        attr = conn.execute(
            "SELECT horizon, portfolio_return_pct, spy_return_pct, realized_edge_vs_spy_bps "
            "FROM attribution WHERE hypothesis_id=? ORDER BY computed_at DESC LIMIT 1",
            (hid,)).fetchone()
        exitq = None
        try:
            exitq = conn.execute(
                "SELECT exit_reason, ret_1d, ret_3d, ret_5d, premature_5d, regret_usd_5d "
                "FROM exit_quality WHERE hypothesis_id=? ORDER BY computed_at DESC LIMIT 1",
                (hid,)).fetchone()
        except sqlite3.OperationalError:
            pass  # table not migrated yet

        ts = _now_iso()
        pid = "PM-" + ts.replace(":", "").replace("-", "") + "-" + hid[-18:]
        thesis = {
            "theme": theme,
            "summary": (h["thesis_summary"] or "")[:240],
            "resolution": {"state": h["resolved_state"], "at": h["resolved_at"],
                           "grade_note": (h["archivist_grade"] or "")[:200]},
            "ticker": ticker,
        }
        expression = {
            "exit_lane": lane or None,
            "attribution": dict(attr) if attr else None,
        }
        researcher = {
            "exit_quality": dict(exitq) if exitq else None,
            "next_checks": ["compare exit lane regret vs holding to horizon"],
        }
        written.append({"postmortem_id": pid, "hypothesis_id": hid,
                        "theme": theme, "ticker": ticker})
        if a.dry_run:
            continue
        conn.execute(
            "INSERT INTO postmortems (id, hypothesis_id, resolved_at, grade, "
            "thesis_analysis_json, expression_analysis_json, critic_analysis_json, "
            "researcher_analysis_json, external_mechanism_check_json, experiment_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, hid, h["resolved_at"] or ts, h["resolved_state"] or "unknown",
             json.dumps(thesis), json.dumps(expression), json.dumps({}),
             json.dumps(researcher), json.dumps({"status": "pending"}),
             "world_model_v1"))
        aid = "AUDIT-" + ts.replace(":", "").replace("-", "") + "-" + pid[:24]
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "rationale_concise) VALUES (?, ?, 'archivist', 'postmortem', ?, "
            "'write_postmortem', ?)",
            (aid, ts, pid, f"{theme}: {ticker} {hid} resolved={h['resolved_state']}"))
    if not a.dry_run and written:
        conn.commit()

    print(json.dumps({"written": len(written), "dry_run": a.dry_run,
                      "rows": written}, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
