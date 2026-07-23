#!/usr/bin/env python3
"""grade_resolutions.py — outcome-grade challenges and resolver decisions. No organ ungraded.

Two judgment streams currently run on trust:
  * the CRITIC's challenges of theses (measured 2026-07-23 by resolver review: 59/66 false
    alarms — but that was LLM-vs-LLM; this grades against the MARKET), and
  * the RESOLVER's HOLD/CLOSE decisions themselves.

Deterministic grading, market-relative, as events mature (>= MATURITY_TD trading days):
  * A CHALLENGE at time t on a thesis with direction d: if the name's forward excess vs SPY
    in direction d over the next 10td is > +TOL, the thesis kept working — the challenge was
    a FALSE ALARM. Below -TOL: VINDICATED. Between: NEUTRAL.
  * A resolver HOLD at t: forward excess in d > +TOL -> CORRECT (kept a working thesis);
    < -TOL -> WRONG. A resolver CLOSE at t: forward excess in d < -TOL -> CORRECT (abandoned
    a dying thesis); > +TOL -> WRONG. Expiries are hygiene, not judged.

Writes ONE summary JSON to state/resolution-grades.json (integrity_check reads it and turns
the false-alarm rate into a standing scoreboard line) and appends per-event grades to
state/resolution-grades.jsonl for the record. Reads the live store read-only.

  python3 grade_resolutions.py            # grade everything mature, write summary
  python3 grade_resolutions.py --dry-run  # print, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors.marketdata import daily_bars  # noqa: E402

DB_PATH = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
OUT_SUMMARY = os.path.expanduser("~/.openclaw/state/resolution-grades.json")
OUT_LEDGER = os.path.expanduser("~/.openclaw/state/resolution-grades.jsonl")
HORIZON_TD = 10          # grade on 10 trading days of forward excess
MATURITY_TD = 12         # need this many forward bars before an event is gradable
TOL_PCT = 2.0            # |excess| below this = NEUTRAL (noise band)
_BEAR = ("short", "bear", "downside", "de-rat", "avoid", "overvalued")

_bars_cache: dict[str, list[dict]] = {}


def _bars(sym: str) -> list[dict]:
    if sym not in _bars_cache:
        try:
            _bars_cache[sym] = [b for b in daily_bars(sym) if b.get("c")]
        except Exception:
            _bars_cache[sym] = []
    return _bars_cache[sym]


def _fwd_excess(ticker: str, date: str, direction: int) -> float | None:
    """Thesis-direction excess vs SPY over HORIZON_TD from the first bar >= date."""
    tb, sb = _bars(ticker), _bars("SPY")
    if not tb or not sb:
        return None

    def _entry(bars):
        for i, b in enumerate(bars):
            if b["t"] >= date:
                return i
        return None

    ti, si = _entry(tb), _entry(sb)
    if ti is None or si is None or ti + MATURITY_TD >= len(tb) or si + MATURITY_TD >= len(sb):
        return None  # not mature yet
    t_ret = (float(tb[ti + HORIZON_TD]["c"]) / float(tb[ti]["c"]) - 1.0) * 100.0
    s_ret = (float(sb[si + HORIZON_TD]["c"]) / float(sb[si]["c"]) - 1.0) * 100.0
    return direction * (t_ret - s_ret)


def _thesis_meta(conn, hyp_id: str) -> tuple[str | None, int]:
    row = conn.execute(
        "SELECT tickers, thesis_summary, rationale_concise FROM hypotheses WHERE id=?", (hyp_id,)
    ).fetchone()
    if not row:
        return None, 1
    try:
        ticker = (json.loads(row["tickers"] or "[]") or [None])[0]
    except ValueError:
        ticker = None
    text = ((row["thesis_summary"] or "") + " " + (row["rationale_concise"] or "")).lower()
    direction = -1 if any(w in text for w in _BEAR) else 1
    return (str(ticker).upper() if ticker else None), direction


def grade(conn) -> dict:
    events = conn.execute(
        "SELECT entity_id hid, action, timestamp FROM audits "
        "WHERE (after_state='challenged' OR action IN ('resolve_hold','resolve_close')) "
        "ORDER BY timestamp"
    ).fetchall()
    graded = []
    for ev in events:
        kind = "challenge" if ev["action"] not in ("resolve_hold", "resolve_close") else ev["action"]
        ticker, direction = _thesis_meta(conn, ev["hid"])
        if not ticker:
            continue
        x = _fwd_excess(ticker, ev["timestamp"][:10], direction)
        if x is None:
            continue  # immature or no data
        if kind == "challenge":
            verdict = "false_alarm" if x > TOL_PCT else ("vindicated" if x < -TOL_PCT else "neutral")
        elif kind == "resolve_hold":
            verdict = "correct" if x > TOL_PCT else ("wrong" if x < -TOL_PCT else "neutral")
        else:  # resolve_close — correct if the thesis kept dying after abandonment
            verdict = "correct" if x < -TOL_PCT else ("wrong" if x > TOL_PCT else "neutral")
        graded.append({"kind": kind, "hid": ev["hid"], "ticker": ticker, "dir": direction,
                       "at": ev["timestamp"][:10], "fwd_excess_pct": round(x, 2), "verdict": verdict})

    def _bucket(kind_prefix):
        rows = [g for g in graded if g["kind"].startswith(kind_prefix)]
        out = {"n_graded": len(rows)}
        for v in ("false_alarm", "vindicated", "correct", "wrong", "neutral"):
            n = sum(1 for r in rows if r["verdict"] == v)
            if n:
                out[v] = n
        if rows:
            out["avg_fwd_excess_pct"] = round(sum(r["fwd_excess_pct"] for r in rows) / len(rows), 2)
        return out

    challenges = _bucket("challenge")
    decisive = [g for g in graded if g["kind"] == "challenge" and g["verdict"] != "neutral"]
    if decisive:
        challenges["false_alarm_rate_decisive"] = round(
            sum(1 for g in decisive if g["verdict"] == "false_alarm") / len(decisive), 3)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_td": HORIZON_TD, "tolerance_pct": TOL_PCT,
        "challenges": challenges,
        "resolver_hold": _bucket("resolve_hold"),
        "resolver_close": _bucket("resolve_close"),
        "events": graded,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        report = grade(conn)
    finally:
        conn.close()
    events = report.pop("events")
    print(json.dumps(report, indent=1))
    if not args.dry_run:
        with open(OUT_SUMMARY, "w") as fh:
            json.dump(report, fh, indent=1)
        with open(OUT_LEDGER, "a") as fh:
            for g in events:
                fh.write(json.dumps({**g, "graded_at": report["generated_at"]}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
