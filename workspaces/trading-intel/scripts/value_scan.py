#!/usr/bin/env python3
"""Valuation-first hypothesis origination (D59) — value as an ENGINE, not a brake.

Operator finding (2026-07-10): the desk computed full valuations every pass
(DCF + multiples -> margin_of_safety, 22k applicable rows) but only the critic
(brake on rich names) and predict (band adjust) ever read them. Nothing
ORIGINATED ideas from undervaluation — the funnel was catalyst-first only.
"We are trying to find alpha here, not just trade the news."

Each run: rank applicable valuations by margin_of_safety × confidence and
author up to MAX_NEW hypotheses for names that also show an INFLECTION —
either price reclaiming its 50d SMA or analysts revising up (pt_rev_60d > 0).
Undervaluation alone is a value trap screen; undervaluation + evidence the
market is starting to agree is the META case the desk kept missing.

The lane is born with learning wired: two value mechanisms (neutral priors)
are seeded, thesis prose embeds their names verbatim (the D57 linker's tier-1
match), and falsifiers are concrete data conditions the researcher can check.

  python3 value_scan.py [--dry-run] [--max N]
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")

MIN_MOS = 0.25          # >=25% below blended fair value
MIN_CONF = 0.20         # valuation confidence floor
MAX_NEW = 3             # per run
COOLDOWN_DAYS = 14      # don't re-author a name that had any hypothesis recently

MECHANISMS = [
    {"id": "value_gap_reversion__quarter_63d",
     "name": "valuation gap reversion: deep margin-of-safety names re-rate toward fair value",
     "antecedent": "undervalued cheap margin of safety discount fair value",
     "consequent": "re-rating mean reversion toward fair value"},
    {"id": "value_inflection__quarter_63d",
     "name": "value with inflection: undervalued name reclaiming trend as the market starts to agree",
     "antecedent": "undervalued cheap plus trend reclaim inflection analyst revision",
     "consequent": "re-rating with momentum confirmation"},
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_mechanisms(conn) -> None:
    for m in MECHANISMS:
        conn.execute(
            "INSERT OR IGNORE INTO mechanisms (id, created_at, created_by, name, antecedent_class, "
            "consequent_class, direction, horizon, prior_alpha, prior_beta, observed_hits, "
            "observed_misses, posterior_mean, half_life_days, status, experiment_id) "
            "VALUES (?, ?, 'quant', ?, ?, ?, 'long', 'quarter_63d', 1.0, 1.0, 0, 0, 0.5, 90, "
            "'active', 'world_model_v1')",
            (m["id"], _now(), m["name"], m["antecedent"], m["consequent"]))


def _sma50_state(feat_conn, ticker: str) -> tuple[float, float] | None:
    """(last_close, sma50) from the local price cache — no network."""
    import mechanism_backtest as mb
    try:
        td = mb.load_ticker(feat_conn, ticker)
    except Exception:
        return None
    dk = td["dates"]
    if len(dk) < 55:
        return None
    closes = [td["close"][d] for d in dk[-50:]]
    return td["close"][dk[-1]], sum(closes) / 50.0


def _pt_rev(feat_conn, ticker: str) -> float | None:
    try:
        r = feat_conn.execute(
            "SELECT value FROM features WHERE ticker=? AND feature='pt_rev_60d' "
            "ORDER BY as_of DESC LIMIT 1", (ticker,)).fetchone()
        return float(r[0]) if r else None
    except sqlite3.OperationalError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max", type=int, default=MAX_NEW)
    a = ap.parse_args(argv)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    feat = sqlite3.connect(f"file:{FEAT}?mode=ro", uri=True)

    if not a.dry_run:
        _seed_mechanisms(conn)

    # exclusions: open exposure or any recent hypothesis on the name
    busy = {r[0].upper() for r in conn.execute(
        "SELECT ticker FROM positions WHERE state IN ('opening','open','scaling','trimming')")}
    busy |= {r[0].upper() for r in conn.execute(
        "SELECT ticker FROM trade_intents WHERE state IN "
        "('proposed','critic_review','risk_review','approved','submitted','partial')")}
    recent = {t.upper() for row in conn.execute(
        f"SELECT tickers FROM hypotheses WHERE created_at >= datetime('now','-{COOLDOWN_DAYS} days')")
        for t in json.loads(row[0] or "[]")}

    cands = conn.execute(
        "SELECT ticker, price, fair_value, margin_of_safety, confidence, zone, pe, p_fcf, "
        "implied_growth, growth_assumed FROM valuations "
        "WHERE as_of = (SELECT MAX(as_of) FROM valuations) AND applicable=1 "
        "AND margin_of_safety >= ? AND confidence >= ? "
        "ORDER BY margin_of_safety * confidence DESC LIMIT 40", (MIN_MOS, MIN_CONF)).fetchall()

    out, authored = [], 0
    for v in cands:
        if authored >= a.max:
            break
        t = v["ticker"].upper()
        if t in busy or t in recent:
            continue
        sma = _sma50_state(feat, t)
        if not sma:
            continue
        last, sma50 = sma
        ptr = _pt_rev(feat, t)
        inflect_trend = last > sma50
        inflect_analyst = ptr is not None and ptr > 0
        if not (inflect_trend or inflect_analyst):
            out.append({"ticker": t, "mos": round(v["margin_of_safety"], 2), "skip": "no inflection (value-trap screen)"})
            continue

        inflection_bits = []
        if inflect_trend:
            inflection_bits.append(f"price {last:.2f} above 50d SMA {sma50:.2f}")
        if inflect_analyst:
            inflection_bits.append(f"analyst targets revised up ({ptr:+.2f} 60d)")
        mech_names = MECHANISMS[1]["name"] if inflect_trend else MECHANISMS[0]["name"]
        thesis = (f"Long {t}: valuation-first origination — trading {v['margin_of_safety']*100:.0f}% below blended "
                  f"fair value {v['fair_value']:.2f} (price {v['price']:.2f}, zone {v['zone']}, "
                  f"PE {v['pe'] or 'n/a'}, P/FCF {v['p_fcf'] or 'n/a'}; market implies "
                  f"{(v['implied_growth'] or 0)*100:.1f}% growth vs {(v['growth_assumed'] or 0)*100:.1f}% assumed). "
                  f"Inflection: {'; '.join(inflection_bits)}. "
                  f"Mechanisms: {MECHANISMS[0]['name']}; {mech_names}.")
        rec = {"ticker": t, "mos": round(v["margin_of_safety"], 2), "conf": round(v["confidence"], 2),
               "inflection": inflection_bits, "authored": not a.dry_run}
        out.append(rec)
        authored += 1
        if a.dry_run:
            continue

        hid = f"hyp-val-{uuid.uuid4().hex[:16]}"
        conn.execute(
            "INSERT INTO hypotheses (id, created_at, created_by, tickers, thesis_summary, state, "
            "confidence, time_horizon) VALUES (?, ?, 'quant', ?, ?, 'raw', ?, 'trend_1_3m')",
            (hid, _now(), json.dumps([t]), thesis,
             "medium" if v["confidence"] >= 0.3 else "low"))
        ev = [("margin_of_safety", f"{v['margin_of_safety']:.3f}"),
              ("fair_value_blend", f"{v['fair_value']:.2f}"),
              ("pe_ttm", str(v["pe"])), ("p_fcf", str(v["p_fcf"])),
              ("implied_vs_assumed_growth", f"{v['implied_growth']}/{v['growth_assumed']}")]
        for ind, val in ev:
            conn.execute(
                "INSERT INTO hypothesis_evidence (id, hypothesis_id, indicator, value, source, "
                "retrieved_at, signal_type) VALUES (?, ?, ?, ?, 'valuation.py universe', ?, 'fundamental')",
                (f"ev-{uuid.uuid4().hex[:12]}", hid, ind, val, _now()))
        for cond in (f"latest valuations row for {t} shows margin_of_safety < 0.05 (gap closed or thesis wrong)",
                     f"{t} closes below its 50d SMA by >5% (inflection failed)"):
            conn.execute(
                "INSERT INTO falsifier_signals (id, hypothesis_id, condition, monitor_frequency, "
                "current_status, updated_at, source_ref) VALUES (?, ?, ?, 'daily', 'monitoring', ?, 'value_scan')",
                (f"fals-{uuid.uuid4().hex[:12]}", hid, cond, _now()))
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, before_state, "
            "after_state, rationale_concise) VALUES (?, ?, 'quant', 'hypothesis', ?, 'author_value_scan', "
            "NULL, 'raw', ?)",
            (f"AUDIT-{_now().replace(':','').replace('-','')}-{uuid.uuid4().hex[:8]}", _now(), hid,
             f"value_scan: {t} mos={v['margin_of_safety']:.2f} conf={v['confidence']:.2f} "
             f"inflection={'trend' if inflect_trend else ''}{'+analyst' if inflect_analyst else ''}"))
        conn.commit()

    print(json.dumps({"candidates": len(cands), "results": out, "authored": authored,
                      "dry_run": a.dry_run}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
