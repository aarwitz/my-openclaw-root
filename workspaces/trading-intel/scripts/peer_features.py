#!/usr/bin/env python3
"""peer_features.py — economic-link momentum (P4 of the alpha-engine roadmap).

Cohen–Frazzini ("Economic Links and Predictable Returns"): returns of economically
linked firms predict a stock's OWN future return — the market is slow to propagate
news across links. True supply-chain links are premium data; v1 proxies links with
FMP's similarity peers (sector/size/business comps), which still carry the
propagation effect at monthly horizons.

Feature (point-in-time, as_of = D, source='peer'):
  peer_mom_21d   mean trailing 21-trading-day SPY-relative return of the name's
                 peers as of D. Expected sign per the paper: POSITIVE (peer
                 strength propagates to the name with a lag).

Also refreshes the KG: `ticker -peer_of-> ticker` edges land in kg_edges via
knowledge_graph.py's tables so the researcher/why-engine can traverse them.

  python3 peer_features.py backfill --top-n 300 --start 2024-01-01
  python3 peer_features.py daily --top-n 300
"""
from __future__ import annotations

import argparse, bisect, os, sqlite3, sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import fmp        # noqa: E402
import feature_store as fs        # noqa: E402

FEAT_DB = os.path.expanduser("~/.openclaw/state/features.sqlite")
H_MOM = 21
MAX_PEERS = 8
_PX_CACHE: dict[str, dict] = {}


def _peers(symbol: str) -> list[str]:
    try:
        r = fmp._get("stock-peers", {"symbol": symbol}, cache_h=336.0)   # peers drift slowly
        return [d["symbol"] for d in r if d.get("symbol") and d["symbol"] != symbol][:MAX_PEERS]
    except Exception:
        return []


def _px(symbol: str) -> dict | None:
    if symbol in _PX_CACHE:
        return _PX_CACHE[symbol]
    try:
        bars = fs._prices(symbol, 1200)
        d = {"dk": [b["t"] for b in bars], "close": {b["t"]: b["c"] for b in bars}}
    except Exception:
        d = None
    _PX_CACHE[symbol] = d
    return d


def _rel_mom(px: dict, spy: dict, D: str) -> float | None:
    i = bisect.bisect_right(px["dk"], D) - 1
    if i < H_MOM:
        return None
    d1, d0 = px["dk"][i], px["dk"][i - H_MOM]
    c1, c0 = px["close"][d1], px["close"][d0]
    j = bisect.bisect_right(spy["dk"], D) - 1
    if j < H_MOM or not c0:
        return None
    s1, s0 = spy["close"][spy["dk"][j]], spy["close"][spy["dk"][j - H_MOM]]
    if not s0:
        return None
    return (c1 / c0 - 1.0) - (s1 / s0 - 1.0)


def _write_kg_edges(pairs: list[tuple[str, str]]):
    """Best-effort: peer edges into kg_edges if the KG tables exist (nightly rebuild
    re-derives correlation/sector edges; peer edges are additive and idempotent)."""
    try:
        conn = sqlite3.connect(FEAT_DB)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(kg_edges)")]
        if not cols:
            conn.close(); return 0
        n = 0
        for a, b in pairs:
            conn.execute(
                "INSERT OR REPLACE INTO kg_edges(src, rel, dst, weight, evidence) "
                "VALUES(?, 'peer_of', ?, 1.0, 'fmp:stock-peers')", (f"ticker:{a}", f"ticker:{b}"))
            n += 1
        conn.commit(); conn.close()
        return n
    except Exception:
        return 0


def run(names: list[str], dates: list[str]) -> int:
    spy = _px("SPY")
    conn = sqlite3.connect(FEAT_DB)
    rows, kg_pairs = [], []
    for i, t in enumerate(names):
        peers = _peers(t)
        if not peers:
            continue
        kg_pairs += [(t, p) for p in peers]
        series = [(p, _px(p)) for p in peers]
        series = [(p, px) for p, px in series if px]
        for D in dates:
            moms = [m for _, px in series if (m := _rel_mom(px, spy, D)) is not None]
            if len(moms) >= 3:
                rows.append((t, D, "peer_mom_21d", round(sum(moms) / len(moms), 4), D, "peer"))
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(names)} names, {len(rows)} rows", flush=True)
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO features(ticker,as_of,name,value,knowable_at,source) VALUES(?,?,?,?,?,?)", rows)
        conn.commit()
    conn.close()
    ne = _write_kg_edges(kg_pairs)
    print(f"done: {len(rows)} peer_mom_21d rows, {ne} kg peer edges")
    return len(rows)


def _top_names(n):
    conn = sqlite3.connect(f"file:{FEAT_DB}?mode=ro", uri=True)
    names = [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (n,))]
    conn.close()
    return names


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--top-n", type=int, default=300)
    b.add_argument("--start", default="2024-01-01")
    d = sub.add_parser("daily")
    d.add_argument("--top-n", type=int, default=300)
    a = ap.parse_args()
    names = _top_names(a.top_n)
    spy = _px("SPY")
    from datetime import date
    today = date.today().isoformat()
    if a.cmd == "backfill":
        dk = [d for d in spy["dk"] if a.start <= d <= today]
        dates = dk[::5]                        # weekly grid — dense enough for a monthly-horizon panel
    else:
        dates = [spy["dk"][-1]]
    run(names, dates)


if __name__ == "__main__":
    main()
