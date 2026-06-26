#!/usr/bin/env python3
"""Phase B — fuse the QUANT world model with LIVE news into a reasoning brief for the LLM agents.

For each watchlist name it combines:
  * quant: firing calibrated mechanisms (signal_scan), valuation/growth (cheap-for-growth / PEG),
    momentum, drawdown, earnings surprise — from the point-in-time feature store;
  * news: recent sentiment + catalyst flags + themes/entities (Event Registry).

Then it flags the judgment-ready patterns the operator cares about:
  * OVERREACTION_LONG  — cheap-for-growth + intact fundamentals + bearish news  (the GOOG / MSFT pattern)
  * CATALYST_POS / CATALYST_NEG — a concrete news catalyst (contract, M&A, launch, probe, …)
  * QUANT_CONVICTION   — many calibrated mechanisms firing

This is ADVISORY context. The research LLM agent reasons over it (full judgment — over/under-reaction,
narrative vs fundamentals) and emits hypotheses; the Risk gate + closed loop do the rest. Writes a
JSON brief to state/catalyst_brief.json and prints a readable summary.

  python3 catalyst_scan.py [--watchlist ...] [--days 14]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import eventregistry, fmp, x as xsocial   # noqa: E402
import signal_scan                          # noqa: E402

OUT = os.path.expanduser("~/.openclaw/state/catalyst_brief.json")
FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
POS = ["contract", "award", "pentagon", "deal", "wins", "partnership", "acquir", "beat", "upgrade",
       "raises", "surge", "record", "breakthrough", "approval", "buyback"]
NEG = ["lawsuit", "probe", "investigation", "recall", "downgrade", "cuts", "miss", "plunge",
       "layoff", "fraud", "warning", "explosion", "crash", "halt", "delay"]


# Phase A: sector/industry relative strength via proxy ETFs (catches supercycles — semis, energy, …)
SECTOR_ETF = [("semiconduct", "SMH"), ("technolog", "XLK"), ("energy", "XLE"), ("financ", "XLF"),
              ("health", "XLV"), ("consumer cyclical", "XLY"), ("consumer defensive", "XLP"),
              ("industrial", "XLI"), ("materi", "XLB"), ("utilit", "XLU"), ("real estate", "XLRE"),
              ("communication", "XLC")]


def _rel63(etf, cache):
    """ETF trailing-63d return minus SPY's (sector tailwind %, point-in-time from prices)."""
    if etf in cache:
        return cache[etf]
    def r63(sym):
        try:
            s = sorted(fmp.historical_price(sym), key=lambda r: r["date"])
            return s[-1]["close"] / s[-64]["close"] - 1 if len(s) > 64 else None
        except Exception:
            return None
    if "__SPY__" not in cache:
        cache["__SPY__"] = r63("SPY")
    e, sp = r63(etf), cache["__SPY__"]
    cache[etf] = round(100 * (e - sp), 2) if (e is not None and sp is not None) else None
    return cache[etf]


def sector_strength(sector, industry, cache):
    key = ((industry or "") + " " + (sector or "")).lower()
    for sub, etf in SECTOR_ETF:
        if sub in key:
            return etf, _rel63(etf, cache)
    return None, None


def _latest(conn, ticker, name):
    r = conn.execute("SELECT value FROM features WHERE ticker=? AND name=? ORDER BY as_of DESC LIMIT 1",
                     (ticker, name)).fetchone()
    return r[0] if r else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default=",".join(signal_scan.DEFAULT_WATCHLIST))
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    names = [s.strip().upper() for s in a.watchlist.split(",") if s.strip()]

    sig_rows, _ = signal_scan.scan(names, min_fired=1)
    sig = {r["ticker"]: r for r in sig_rows}
    conn = sqlite3.connect(FEAT)

    brief = []
    etf_cache = {}
    for t in names:
        growth = _latest(conn, t, "revenue_growth_yoy")
        eps_ttm = _latest(conn, t, "eps_ttm")
        surprise = _latest(conn, t, "eps_surprise_pct")
        try:
            prof = fmp.profile(t)
            p0 = prof[0] if prof else {}
            cname = p0.get("companyName") or t
            price = p0.get("price")
            sector_name = p0.get("sector")
            sec_etf, sec_rel = sector_strength(sector_name, p0.get("industry"), etf_cache)
        except Exception:
            cname, price, sector_name, sec_etf, sec_rel = t, None, None, None, None
        pe = (price / eps_ttm) if (price and eps_ttm and eps_ttm > 0) else None
        peg = (pe / (growth * 100)) if (pe and growth and growth > 0) else None
        cheap_for_growth = bool(peg is not None and peg < 1.5)

        try:
            arts = eventregistry.recent_news(cname, days=a.days, count=20)
        except Exception:
            arts = []
        sents = [x["sentiment"] for x in arts if x.get("sentiment") is not None]
        avg_sent = round(sum(sents) / len(sents), 3) if sents else None
        titles = " ".join((x.get("title") or "").lower() for x in arts)
        pos_hit = sorted({k for k in POS if k in titles})
        neg_hit = sorted({k for k in NEG if k in titles})
        themes = sorted({c[0] for x in arts for c in x.get("concepts", []) if c[1] in ("org", "wiki") and c[0]})[:6]
        x_z, x_count = xsocial.recent_attention(t)   # advisory social-attention spike (NOT sizing)

        s = sig.get(t, {})
        flags = []
        if cheap_for_growth and growth and growth > 0 and avg_sent is not None and avg_sent < -0.05:
            flags.append("OVERREACTION_LONG")          # cheap-for-growth + bearish news (GOOG/MSFT)
        if pos_hit and (avg_sent or 0) > 0.15:
            flags.append("CATALYST_POS")
        if neg_hit and (avg_sent or 0) < -0.15:
            flags.append("CATALYST_NEG")
        if s.get("n_fired", 0) >= 6 and s.get("p_long", 0) >= 0.65:
            flags.append("QUANT_CONVICTION")
        if x_z is not None and x_z >= 2.5:
            flags.append("SOCIAL_SPIKE")               # abnormal X attention — investigate catalyst
        if not flags:
            continue
        if sec_rel is not None and sec_rel > 5:
            flags.append("SECTOR_TAILWIND")
        brief.append({
            "ticker": t, "company": cname, "flags": flags,
            "quant": {"p_long": round(s.get("p_long", 0), 3), "n_mechs": s.get("n_fired", 0),
                      "pe_ttm": round(pe, 1) if pe else None, "rev_growth_yoy": round(growth, 3) if growth else None,
                      "peg": round(peg, 2) if peg else None, "eps_surprise": round(surprise, 3) if surprise else None},
            "sector": {"name": sector_name, "etf": sec_etf, "rel_63d_pct": sec_rel},
            "news": {"avg_sentiment": avg_sent, "n_articles": len(arts), "pos_catalysts": pos_hit,
                     "neg_catalysts": neg_hit, "themes": themes,
                     "headlines": [x.get("title") for x in arts[:3]]},
            "social": {"x_attention_z": x_z, "x_mentions_today": x_count},
        })
    conn.close()
    order = {"OVERREACTION_LONG": 0, "CATALYST_POS": 1, "CATALYST_NEG": 2, "QUANT_CONVICTION": 3, "SOCIAL_SPIKE": 4}
    brief.sort(key=lambda b: min(order.get(f, 9) for f in b["flags"]))
    json.dump({"generated": __import__("datetime").datetime.utcnow().isoformat() + "Z", "brief": brief},
              open(OUT, "w"), indent=2)

    print(f"CATALYST BRIEF — {len(brief)} flagged names (quant x news), -> {OUT}\n")
    for b in brief:
        q, n = b["quant"], b["news"]
        sec = b.get("sector", {})
        print(f"  {b['ticker']:6} {','.join(b['flags'])}")
        print(f"         quant: p_long={q['p_long']} mechs={q['n_mechs']} pe={q['pe_ttm']} growth={q['rev_growth_yoy']} peg={q['peg']}")
        print(f"         sector:{sec.get('name')} ({sec.get('etf')}) rel63d={sec.get('rel_63d_pct')}%")
        print(f"         news : sent={n['avg_sentiment']} pos={n['pos_catalysts']} neg={n['neg_catalysts']}")
        if n["headlines"]:
            print(f"         head : {n['headlines'][0]}")
        soc = b.get("social", {})
        if soc.get("x_attention_z") is not None:
            print(f"         social: X attention z={soc['x_attention_z']} ({soc.get('x_mentions_today')} mentions today)")
    print("\n(advisory — the research agent reasons over this brief to judge over/under-reaction and emit hypotheses)")


if __name__ == "__main__":
    main()
