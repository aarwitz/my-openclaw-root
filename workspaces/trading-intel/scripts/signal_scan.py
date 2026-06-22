#!/usr/bin/env python3
"""Deterministic feature-trigger activation — fire the calibrated mechanisms from each ticker's
CURRENT feature values (no LLM thesis required), and rank names by combined conviction.

This is the world model used as a *scanner*: for each watchlist name it reads the latest
point-in-time features, checks which `calibrated_mechanisms` (features.sqlite) fire, and combines
their posteriors (log-odds, opposing shorts subtract) into a p_correct + an expected net-alpha.

Read-only / advisory: it emits a ranked signal list. Wiring these into live hypotheses/intents
(so the desk trades them) is the gated next step — this tool is the deterministic activation layer.

  python3 signal_scan.py [--watchlist AAPL,MSFT,...] [--min-fired 2] [--top 25]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import fmp     # noqa: E402
import feature_store as fs     # noqa: E402
import worldmodel as wm        # noqa: E402

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
DEFAULT_WATCHLIST = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "JPM", "UNH",
                     "XOM", "CAT", "WMT", "COST", "KO", "HD", "PG", "JNJ", "CRM", "AMD", "NFLX",
                     "DIS", "BA", "GE", "PFE", "INTC", "MU", "CVX", "ORCL", "QCOM"]


def live_watchlist(top_n=200):
    """DYNAMIC live scan universe — the top-N most liquid active names UNION an explicit watch set
    (`features.sqlite::live_watch`). This is how a ticker DYNAMICALLY enters the live workflow: once it's
    in the feature store + universe (or the live_watch table), it gets scanned → can become a hypothesis →
    trade → be graded into the live ledger. No re-integration, no wipe — adding tickers is purely additive."""
    c = sqlite3.connect(FEAT)
    syms = [r[0] for r in c.execute("SELECT symbol FROM universe WHERE status='active' AND "
            "market_cap IS NOT NULL ORDER BY market_cap DESC LIMIT ?", (top_n,))]
    try:
        syms += [r[0] for r in c.execute("SELECT symbol FROM live_watch")]
    except sqlite3.OperationalError:
        pass
    c.close()
    return list(dict.fromkeys(syms)) or DEFAULT_WATCHLIST


def latest_features(conn, ticker):
    """Most-recent value per feature for a ticker (point-in-time = latest as_of)."""
    out = {}
    for name, as_of, val in conn.execute(
        "SELECT name, as_of, value FROM features WHERE ticker=? ORDER BY as_of", (ticker,)):
        out[name] = (val, as_of)          # later rows overwrite -> latest wins
    return out


def cond_holds(conds, feats):
    for name, op, thr in conds:
        v = feats.get(name)
        if v is None:
            return False
        v = v[0]
        if op == ">" and not v > thr:
            return False
        if op == "<" and not v < thr:
            return False
    return True


def _returns(ticker, n=63):
    try:
        px = fs._prices(ticker, 800)
        c = [b["c"] for b in px[-(n + 1):] if b.get("c")]
        return [c[i] / c[i - 1] - 1 for i in range(1, len(c))] if len(c) > 10 else None
    except Exception:
        return None


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 15:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / (va ** 0.5 * vb ** 0.5)


def _correlation_adjust(rows, kgc, link=0.5):
    """Graded correlation discount using the KNOWLEDGE GRAPH's `correlated_with` edges as the single
    source of truth (replaces the old on-the-fly correlation compute). A missing edge between two names
    means their correlation is below the KG's 0.55 edge floor → treated as uncorrelated (novelty 1).
    Each name's conviction is discounted by its correlation to higher-ranked candidates: novelty =
    1−max corr, adj_edge = exp_edge×novelty; re-ranks by adj_edge; reports effective independent bets."""
    for i, r in enumerate(rows):
        red, link_to = 0.0, None
        for j in range(i):                                  # only compare to HIGHER-ranked names
            c = kgc.get(frozenset((r["ticker"], rows[j]["ticker"])), 0.0)
            if c > red:
                red, link_to = c, j
        r["redundancy"] = round(max(0.0, red), 2)
        r["novelty"] = round(1.0 - max(0.0, red), 2)
        r["adj_edge"] = round(r["exp_edge"] * r["novelty"], 2)
        r["cluster_id"] = rows[link_to]["cluster_id"] if (link_to is not None and red >= link) else i
    size = {}
    for r in rows:
        size[r["cluster_id"]] = size.get(r["cluster_id"], 0) + 1
    for r in rows:
        r["cluster_size"] = size[r["cluster_id"]]
    eff_bets = sum(r["novelty"] for r in rows)
    rows.sort(key=lambda r: -r["adj_edge"])                 # diversified priority
    return rows, eff_bets


def _current_regimes():
    """Current regime buckets from regime_brief.json (run regime_brief.py to refresh)."""
    try:
        b = json.load(open(os.path.expanduser("~/.openclaw/state/regime_brief.json")))
        m = b.get("macro", {})
        out = []
        if m.get("vix") is not None:
            out.append("vix_hi" if m["vix"] > 22 else "vix_lo")
        rc = m.get("rate_10y_chg_63d")
        if rc is not None:
            out.append("rate_up" if rc > 0.2 else ("rate_dn" if rc < -0.2 else "rate_flat"))
        return out
    except Exception:
        return []


def scan(names, min_fired=1):
    """Return (ranked_rows, n_mechanisms). Each row: ticker, p_long, exp_edge, n_fired, price,
    direction, and `fired` = the firing mechanisms (id/direction/horizon/posterior)."""
    conn = sqlite3.connect(FEAT)
    conn.row_factory = sqlite3.Row
    mechs = [dict(r) for r in conn.execute("SELECT * FROM calibrated_mechanisms")]
    for m in mechs:
        m["conds"] = json.loads(m["conds_json"])
    rw = {}                                          # (id,horizon) -> redundancy_weight (1/cluster_size)
    try:
        for mid, hz, w in conn.execute("SELECT mechanism_id, horizon, redundancy_weight FROM mechanism_clusters"):
            rw[(mid, hz)] = w
    except sqlite3.OperationalError:
        pass
    reg = {}                                          # (id,horizon,regime) -> alpha_pct
    try:
        for mid, hz, rgm, a in conn.execute("SELECT mechanism_id, horizon, regime, alpha_pct FROM mechanism_regime"):
            reg[(mid, hz, rgm)] = a
    except sqlite3.OperationalError:
        pass
    cur_reg = _current_regimes()
    kgc = {}                                          # knowledge-graph correlation edges (single source of truth)
    try:
        for s, d, w in conn.execute("SELECT src, dst, weight FROM kg_edges WHERE rel='correlated_with'"):
            kgc[frozenset((s.split(":")[1], d.split(":")[1]))] = w
    except sqlite3.OperationalError:
        pass

    def regime_fit(mid, hz):                          # up/down-weight by edge in the CURRENT regime vs ALL
        base = reg.get((mid, hz, "ALL"))
        if not base or base <= 0 or not cur_reg:
            return 1.0
        fits = [max(0.0, min(2.0, reg[(mid, hz, rgm)] / base)) for rgm in cur_reg if (mid, hz, rgm) in reg]
        return sum(fits) / len(fits) if fits else 1.0
    rows = []
    for t in names:
        feats = latest_features(conn, t)
        if not feats:
            continue
        try:
            px = fs._prices(t, 800)
            close = px[-1]["c"] if px else None
        except Exception:
            close = None
        eps = feats.get("eps_ttm")
        if close and eps and eps[0] and eps[0] > 0:
            feats["pe_ttm"] = (close / eps[0], eps[1])
        fired = [m for m in mechs if cond_holds(m["conds"], feats)]
        if len(fired) < min_fired:
            continue
        terms, edge = [], 0.0
        for m in fired:
            p = m["posterior_mean"]
            # combine #2 redundancy down-weight + #3 regime fit (works in the CURRENT regime?)
            w = rw.get((m["id"], m["horizon"]), 1.0) * regime_fit(m["id"], m["horizon"])
            na = (m["net_alpha_pct"] or 0)
            if m["direction"] == "short":
                p = 1.0 - p
                edge -= na * w
            else:
                edge += na * w
            terms.append((p, min(1.0, abs(na) / 2.0 + 0.3) * w))
        p_long, _ = wm.combine_p(0.5, terms, 1.0)
        rows.append({"ticker": t, "p_long": p_long, "exp_edge": edge, "n_fired": len(fired),
                     "price": round(close, 2) if close else None,
                     "direction": "long" if p_long >= 0.5 else "short",
                     "fired": [{"id": m["id"], "direction": m["direction"], "horizon": m["horizon"],
                                "posterior": m["posterior_mean"], "rationale": m["rationale"]} for m in fired]})
    conn.close()
    rows.sort(key=lambda r: -r["exp_edge"])
    rows, _ = _correlation_adjust(rows, kgc)        # KG-driven novelty discount + re-rank by adj_edge
    return rows, len(mechs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default=",".join(DEFAULT_WATCHLIST))
    ap.add_argument("--min-fired", type=int, default=1)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    names = [s.strip().upper() for s in a.watchlist.split(",") if s.strip()]
    rows, nmech = scan(names, a.min_fired)
    print(f"scanning {len(names)} names against {nmech} calibrated mechanisms\n")
    print(f"  {'ticker':7} {'p_long':>6} {'edge%':>6} {'adj':>6} {'nov':>5} {'fired':>5} {'price':>8}  top mechanisms")
    for r in rows[:a.top]:
        mids = ", ".join(sorted(set(f["id"] for f in r["fired"]))[:4])
        print(f"  {r['ticker']:7} {r['p_long']:>6.3f} {r['exp_edge']:>6.1f} {r['adj_edge']:>6.1f} "
              f"{r['novelty']:>5.2f} {r['n_fired']:>5} {str(r['price']):>8}  {mids}")
    eff = sum(r["novelty"] for r in rows)
    from collections import defaultdict
    cg = defaultdict(list)
    for r in rows:
        cg[r["cluster_id"]].append(r["ticker"])
    multi = sorted((v for v in cg.values() if len(v) > 1), key=lambda v: -len(v))
    print(f"\nCORRELATION-ADJUSTED — {len(rows)} candidates ≈ {eff:.1f} EFFECTIVE independent bets. "
          f"'adj' = edge×novelty (correlated siblings discounted); ranked by adj.")
    if multi:
        print("  correlated groups (corr≥0.5): " + " | ".join(", ".join(m) for m in multi[:5]))
    print("\n(advisory — size by EFFECTIVE bets, not name count)")


if __name__ == "__main__":
    main()
