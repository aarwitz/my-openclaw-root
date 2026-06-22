#!/usr/bin/env python3
"""Live integration of the empirically-calibrated mechanism set into the LIVE world model.

INCREMENTAL BY DEFAULT (2026-06-19) — this NEVER wipes the live learning ledger:
  * mechanism that PERSISTS (same id)   -> UPDATE its backtest prior (prior_alpha/prior_beta) but
    PRESERVE its observed_hits/observed_misses/last_observed_at; posterior = beta_mean(prior+obs).
  * mechanism that is NEW               -> INSERT with the backtest prior, zero live obs.
  * live mechanism no longer calibrated -> mark status='deprecated' (kept for history; stops being used).
  * `predictions`, `mechanism_observations`, `attribution`, `postmortems`, `patterns`, hypothesis
    grades are LEFT UNTOUCHED — the desk keeps everything it has learned from its own trades.
This is what lets us refresh the mechanism set (after a new discovery run) AND keep accruing live learning.
The two-learning-rates blend (calibrate.py) then shrinks the refreshed prior as live obs accumulate.

  python3 integrate_calibrated.py            # incremental upsert (default; preserves the ledger)
  python3 integrate_calibrated.py --reset    # full re-bootstrap (the old behaviour; WIPES the ledger)

Adding TICKERS never goes through this script at all — tickers enter via the feature store + the live
scan watchlist, and feed the ledger by trading + being graded (observations are keyed by mechanism).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

LIVE = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
PSEUDO_N = 40.0
HZN = {"swing_5d": "swing_1_5d", "month_21d": "position_1_4w", "quarter_63d": "trend_1_3m"}
DIRMAP = {"long": "long", "short": "short", "long_short": "long"}

KW = [
    ("oversold", ("oversold pullback dip drawdown", "mean reversion rebound")),
    ("drawdown", ("deep drawdown oversold selloff", "mean reversion recovery")),
    ("rsi", ("oversold overbought rsi reversal", "short-term reversal")),
    ("vol_20d_annual_hi", ("high volatility elevated risk", "volatility rebound")),
    ("vol_20d_annual_lo", ("low volatility quiet", "low-vol underperformance")),
    ("pe_ttm_lo", ("cheap low pe valuation undervalued value", "valuation rerating")),
    ("pe_ttm_hi", ("expensive rich high pe valuation", "multiple compression")),
    ("cheap_pe", ("cheap low pe valuation undervalued value", "valuation rerating")),
    ("net_margin", ("low margin profitability turnaround", "margin recovery")),
    ("revenue_growth", ("revenue growth sales growth", "growth continuation")),
    ("growth", ("revenue growth sales growth", "growth continuation")),
    ("momentum", ("price momentum trend uptrend", "trend continuation")),
    ("mom_12_1", ("price momentum trend uptrend", "trend continuation")),
    ("dist_sma", ("trend moving average uptrend momentum", "trend continuation")),
    ("earnings", ("earnings beat surprise guidance raise", "post-earnings drift")),
    ("vix", ("high volatility fear capitulation vix", "fear rebound")),
    ("rate_", ("interest rates duration macro", "rate-driven move")),
    ("credit", ("credit spreads risk-off macro", "risk-off move")),
    ("sentiment", ("news sentiment narrative", "sentiment drift")),
    ("days_to_cover", ("short interest crowded squeeze", "short squeeze")),
    ("short_int", ("short interest crowded squeeze", "short squeeze")),
    ("sector", ("sector strength rotation", "sector tailwind")),
    ("rating", ("analyst upgrade rating revision", "rating drift")),
    ("insider", ("insider buying selling form4", "insider signal")),
]


def tokens(mid):
    for key, pair in KW:
        if key in mid:
            return pair
    return ("market signal", "outperformance")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="full re-bootstrap (WIPES the live ledger)")
    a = ap.parse_args()

    cal = sqlite3.connect(FEAT)
    cal.row_factory = sqlite3.Row
    survivors = [dict(r) for r in cal.execute("SELECT * FROM calibrated_mechanisms ORDER BY net_alpha_pct DESC")]
    cal.close()
    if not survivors:
        raise SystemExit("no calibrated_mechanisms found — run promote_mechanisms.py first")

    conn = sqlite3.connect(LIVE, timeout=60.0)
    conn.row_factory = sqlite3.Row
    sys.path.insert(0, os.path.dirname(__file__))
    import worldmodel as wm
    exp = (conn.execute("SELECT experiment_id FROM mechanisms WHERE experiment_id IS NOT NULL LIMIT 1").fetchone()
           or conn.execute("SELECT experiment_id FROM hypotheses WHERE experiment_id IS NOT NULL LIMIT 1").fetchone())
    exp = exp[0] if exp else "default"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def count(t):
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            return "—"

    led_before = {t: count(t) for t in ("mechanism_observations", "predictions", "attribution")}
    existing = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, observed_hits, observed_misses, created_at FROM mechanisms")}
    added = updated = deprecated = 0
    try:
        conn.execute("BEGIN")
        if a.reset:
            for t in ("mechanism_observations", "predictions", "attribution", "postmortems", "patterns"):
                try:
                    conn.execute(f"DELETE FROM {t}")
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute("UPDATE hypotheses SET resolved_state=NULL, resolved_at=NULL, archivist_grade=NULL")
            except sqlite3.OperationalError:
                pass
            conn.execute("DELETE FROM mechanisms")
            existing = {}

        cal_ids = set()
        for s in survivors:
            mid = f'{s["id"]}__{s["horizon"]}'
            cal_ids.add(mid)
            pm = float(s["posterior_mean"])
            pa, pb = round(pm * PSEUDO_N, 4), round((1 - pm) * PSEUDO_N, 4)
            ant, cons = tokens(s["id"])
            note = json.dumps({"calibrated": True, "source": s["source"], "conds": json.loads(s["conds_json"]),
                               "net_alpha_pct": s["net_alpha_pct"], "test_p": s["test_p"],
                               "bonferroni": bool(s["bonf_sig"]), "hit_rate": s["hit_te"],
                               "backtest_n": s["te_n"], "skew_edge": bool(s["skew_edge"]),
                               "refreshed": now})
            if mid in existing:
                # PRESERVE live observations; refresh the backtest prior; re-blend the posterior
                hits = float(existing[mid].get("observed_hits") or 0.0)
                misses = float(existing[mid].get("observed_misses") or 0.0)
                post = round(wm.beta_mean(pa + hits, pb + misses), 6)
                ci_low, ci_high = wm.beta_ci(pa + hits, pb + misses)
                conn.execute(
                    "UPDATE mechanisms SET prior_alpha=?, prior_beta=?, name=?, antecedent_class=?, "
                    "consequent_class=?, direction=?, horizon=?, posterior_mean=?, posterior_ci_low=?, "
                    "posterior_ci_high=?, status='active', notes=? WHERE id=?",
                    (pa, pb, s["rationale"], ant, cons, DIRMAP.get(s["direction"], "long"),
                     HZN.get(s["horizon"], "position_1_4w"), post, round(ci_low, 6), round(ci_high, 6), note, mid))
                updated += 1
            else:
                ci_low, ci_high = wm.beta_ci(pa, pb)
                conn.execute(
                    "INSERT INTO mechanisms(id, created_at, created_by, name, antecedent_class, "
                    "transmission_chain_json, consequent_class, direction, horizon, regime_context, "
                    "prior_alpha, prior_beta, observed_hits, observed_misses, posterior_mean, "
                    "posterior_ci_low, posterior_ci_high, half_life_days, last_observed_at, status, "
                    "notes, experiment_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, now, "archivist", s["rationale"], ant, "[]", cons,
                     DIRMAP.get(s["direction"], "long"), HZN.get(s["horizon"], "position_1_4w"), "any",
                     pa, pb, 0.0, 0.0, round(pm, 6), round(ci_low, 6), round(ci_high, 6), 180.0, None,
                     "active", note, exp))
                added += 1

        # deprecate live mechanisms no longer in the calibrated set (KEEP their obs/history)
        for mid in existing:
            if mid not in cal_ids:
                conn.execute("UPDATE mechanisms SET status='deprecated' WHERE id=?", (mid,))
                deprecated += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    led_after = {t: count(t) for t in led_before}
    print("LIVE INTEGRATION COMPLETE", "(--reset: FULL WIPE)" if a.reset else "(incremental — ledger preserved)")
    print(f"  experiment_id={exp}")
    print(f"  mechanisms: +{added} added, {updated} updated (priors refreshed, obs preserved), "
          f"{deprecated} deprecated -> {count('mechanisms')} live ({count('mechanisms') - deprecated} active)")
    print("  LEDGER (preserved unless --reset):")
    for t in led_before:
        flag = "" if (a.reset or led_before[t] == led_after[t]) else "  <-- CHANGED?!"
        print(f"    {t:24} {str(led_before[t]):>6} -> {led_after[t]}{flag}")
    conn.close()


if __name__ == "__main__":
    main()
