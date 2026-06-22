#!/usr/bin/env python3
"""Point-in-time FEATURE STORE — the foundation under mechanisms/backtests.

Writes ~/.openclaw/state/features.sqlite (separate from live trading-intel.sqlite — safe to
build/test before any reset). One tall table:

    features(ticker, as_of, name, value, knowable_at, source)   PK (ticker, as_of, name)

`as_of` = the date the value first becomes USABLE (= knowable_at). Point-in-time read =
latest row with as_of <= D. This is what makes backtests honest:
  * technical features (Alpaca split-adj bars): as_of = bar date (known at EOD)
  * fundamental features (FMP): as_of = filingDate  (NOT the fiscal-period date — that leaks)
  * earnings-surprise events (FMP): as_of = report date

Also reconstructs survivorship-bias-free S&P 500 membership as-of any date (FMP historical
constituents), so a backtest universe can include names that were later delisted/removed.

  python3 feature_store.py build --universe AAPL,MSFT,... [--days 4000]
  python3 feature_store.py members-asof 2018-01-01
  python3 feature_store.py verify AAPL
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import alpaca, fmp, massive  # noqa: E402

OUT = Path(os.path.expanduser("~/.openclaw/state/features.sqlite"))


def _conn():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(OUT, timeout=60.0)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS features(
        ticker TEXT, as_of TEXT, name TEXT, value REAL, knowable_at TEXT, source TEXT,
        PRIMARY KEY(ticker, as_of, name))""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_feat ON features(ticker, name, as_of)")
    return c


# Phase A: insider, analyst-revision, and sector-strength features (point-in-time) --------------
_SECTOR_ETF = [("semiconduct", "SMH"), ("technolog", "XLK"), ("energy", "XLE"), ("financ", "XLF"),
               ("health", "XLV"), ("consumer cyclical", "XLY"), ("consumer defensive", "XLP"),
               ("industrial", "XLI"), ("materi", "XLB"), ("utilit", "XLU"), ("real estate", "XLRE"),
               ("communication", "XLC")]
_ETF_REL_CACHE: dict = {}


def _rolling_net(events, window_days, key):
    """events: sorted [(date, value)]. Returns [(date, {key: trailing-window net ratio})]."""
    from datetime import date as _d
    out = []
    for i, (d, _) in enumerate(events):
        lo = _d.fromisoformat(d).toordinal() - window_days
        win = [v for dd, v in events if lo <= _d.fromisoformat(dd).toordinal() <= _d.fromisoformat(d).toordinal()]
        pos = sum(v for v in win if v > 0)
        neg = -sum(v for v in win if v < 0)
        if pos + neg > 0:
            out.append((d, {key: (pos - neg) / (pos + neg)}))
    return out


def _insider(symbol):
    try:
        tx = fmp.insider_trading(symbol, limit=300)
    except Exception:
        return []
    ev = []
    for t in tx:
        tt = (t.get("transactionType") or "")
        d = (t.get("filingDate") or "")[:10]
        val = (t.get("securitiesTransacted") or 0) * (t.get("price") or 0)
        if not d or val <= 0:
            continue
        if tt.startswith("P"):
            ev.append((d, val))          # open-market purchase = bullish
        elif tt.startswith("S"):
            ev.append((d, -val))         # sale = bearish
    return _rolling_net(sorted(ev), 180, "insider_net_180d")


def _revisions(symbol):
    try:
        ud = fmp.upgrades_downgrades(symbol, limit=400)
    except Exception:
        return []
    ev = []
    for u in ud:
        a = (u.get("action") or "").lower()
        d = (u.get("date") or "")[:10]
        if d and a in ("upgrade", "downgrade"):
            ev.append((d, 1.0 if a == "upgrade" else -1.0))
    return _rolling_net(sorted(ev), 90, "rating_net_90d")


def _etf_rel_series(etf):
    """date -> (ETF trailing-63d return - SPY trailing-63d return). Cached per ETF."""
    if etf in _ETF_REL_CACHE:
        return _ETF_REL_CACHE[etf]

    def ret63(sym):
        px = sorted(fmp.historical_price(sym), key=lambda r: r["date"])
        d = [r["date"] for r in px]
        c = [r["close"] for r in px]
        return {d[i]: c[i] / c[i - 63] - 1 for i in range(63, len(d)) if c[i - 63]}
    if "__SPY__" not in _ETF_REL_CACHE:
        _ETF_REL_CACHE["__SPY__"] = ret63("SPY")
    spy = _ETF_REL_CACHE["__SPY__"]
    try:
        em = ret63(etf)
    except Exception:
        em = {}
    rel = {d: em[d] - spy[d] for d in em if d in spy}
    _ETF_REL_CACHE[etf] = rel
    return rel


def _sector(symbol):
    try:
        prof = fmp.profile(symbol)
        key = ((prof[0].get("industry") or "") + " " + (prof[0].get("sector") or "")).lower() if prof else ""
    except Exception:
        return []
    etf = next((e for k, e in _SECTOR_ETF if k in key), None)
    if not etf:
        return []
    return [(d, {"sector_rel_63d": v}) for d, v in _etf_rel_series(etf).items()]


def _news_features(rows):
    """rows: [{date, sentiment, relevance}] (historical, any provider). Returns point-in-time
    [(date, {news_sent_7d, news_sent_30d, news_vol_z})] — trailing windows use only past news."""
    from datetime import date as _d
    daily = {}   # date -> [w_sent_sum, w_sum, count]
    for r in rows:
        d = r.get("date")
        if not d:
            continue
        w = max(0.05, float(r.get("relevance") or 0.0))
        a = daily.setdefault(d, [0.0, 0.0, 0])
        a[0] += w * float(r.get("sentiment") or 0.0)
        a[1] += w
        a[2] += 1
    days = sorted(daily)
    sent = {d: (daily[d][0] / daily[d][1] if daily[d][1] else 0.0) for d in days}
    cnt = {d: daily[d][2] for d in days}
    ords = {d: _d.fromisoformat(d).toordinal() for d in days}
    out = []
    for d in days:
        o = ords[d]
        s7 = [sent[x] for x in days if o - 6 <= ords[x] <= o]
        s30 = [sent[x] for x in days if o - 29 <= ords[x] <= o]
        hist = [cnt[x] for x in days if o - 89 <= ords[x] <= o - 1]
        f = {"news_sent_7d": sum(s7) / len(s7) if s7 else None,
             "news_sent_30d": sum(s30) / len(s30) if s30 else None}
        if len(hist) >= 10:
            m = sum(hist) / len(hist)
            sd = (sum((x - m) ** 2 for x in hist) / len(hist)) ** 0.5
            f["news_vol_z"] = (cnt[d] - m) / sd if sd > 0 else 0.0
        out.append((d, {k: v for k, v in f.items() if v is not None}))
    return out


_LEX_POS = set("beat beats raise raises raised surge surges jump jumps soar soars rally record strong "
               "growth wins win won award awarded approval upgrade upgraded outperform bullish gains gain "
               "rises rose tops top exceeds exceed momentum breakout partnership expansion profit profitable "
               "boost boosts rebound recovery accelerate accelerating demand".split())
_LEX_NEG = set("miss misses cut cuts lower lowered plunge plunges slump slumps fall falls drop drops decline "
               "declines weak loss losses lawsuit probe investigation recall downgrade downgraded bearish "
               "warning warns slowdown layoffs fraud halt delay concern concerns risk risks crash sinks "
               "tumble tumbles slashes plummet bankruptcy".split())


def _lex_sentiment(text):
    words = re.findall(r"[a-z]+", (text or "").lower())
    if not words:
        return None
    p = sum(1 for w in words if w in _LEX_POS)
    n = sum(1 for w in words if w in _LEX_NEG)
    return 0.0 if p + n == 0 else (p - n) / (p + n)


def _news(symbol):
    """Historical news features. Prefer MASSIVE (historical articles; AI sentiment where present,
    else deterministic lexicon over title+description) → Alpha Vantage if configured."""
    try:
        from connectors import massive
        if massive.available():
            rows = []
            for a in massive.ticker_news(symbol):
                s = a.get("sentiment")
                if s is None:
                    s = _lex_sentiment(a.get("title", "") + " " + a.get("description", ""))
                if s is not None and a.get("date"):
                    rows.append({"date": a["date"], "sentiment": s, "relevance": a.get("relevance", 1.0)})
            if rows:
                return _news_features(rows)
    except Exception:
        pass
    try:
        from connectors import alphavantage
        if alphavantage.available():
            return _news_features(alphavantage.news_sentiment(symbol))
    except Exception:
        pass
    return []


def _short_interest(symbol):
    """Point-in-time short interest from Massive (FINRA bi-monthly settlements). FINRA disseminates
    each settlement ~8 business days later, so stamp knowable_at = settlement + 8 bdays (never the
    settlement date — that would leak ~1-2 weeks). days_to_cover = squeeze pressure / crowdedness;
    short_int_chg_2m = SI momentum. short-%-of-float omitted (no point-in-time float available)."""
    from datetime import date as _d, timedelta as _td
    try:
        si = massive.short_interest(symbol)
    except Exception:
        return []

    def _plus_bdays(iso, n):
        dt = _d.fromisoformat(iso)
        while n > 0:
            dt += _td(days=1)
            if dt.weekday() < 5:
                n -= 1
        return dt.isoformat()

    out, prev = [], None
    for r in si:
        feats = {}
        if r.get("days_to_cover") is not None:
            feats["days_to_cover"] = float(r["days_to_cover"])
        if prev and prev > 0 and r.get("short_interest"):
            feats["short_int_chg_2m"] = r["short_interest"] / prev - 1.0
        prev = r.get("short_interest") or prev
        if feats:
            out.append((_plus_bdays(r["date"], 8), feats))
    return out


def _prices(symbol, days):
    """Split-adjusted daily prices. Prefer MASSIVE (unthrottled, ~10yr, incl. delisted) → FMP
    (deeper 20yr fallback) → Alpaca (last resort). Shape {t,c,h,v}, oldest first."""
    try:
        from connectors import massive
        if massive.available():
            b = massive.daily_bars(symbol)
            if b:
                return b
    except Exception:
        pass
    try:
        fb = fmp.historical_price(symbol, frm="2004-01-01")
        if fb:
            fb = sorted(fb, key=lambda r: r["date"])
            out = [{"t": r["date"], "c": r["close"], "h": r.get("high") or r["close"],
                    "v": r.get("volume") or 0} for r in fb if r.get("close")]
            if out:
                return out
    except Exception as e:
        print(f"  {symbol}: FMP price fallback ({str(e)[:50]})", file=sys.stderr)
    ab = alpaca.daily_bars(symbol, days=days, adjustment="split")
    return [{"t": b["t"][:10], "c": b["c"], "h": b["h"], "v": b.get("v", 0)} for b in ab]


def _emit(rows, ticker, as_of, knowable_at, source, feats: dict):
    for name, val in feats.items():
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            continue
        rows.append((ticker, as_of, name, float(val), knowable_at, source))


# --- technical series from bars -------------------------------------------------
def _technical(bars):
    """Return list of (date, {feature: value}) from oldest->newest split-adjusted bars."""
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    dates = [b["t"][:10] for b in bars]
    n = len(c)
    # Wilder RSI(14)
    rsi = [None] * n
    if n > 15:
        gains = [max(0.0, c[i] - c[i - 1]) for i in range(1, n)]
        losses = [max(0.0, c[i - 1] - c[i]) for i in range(1, n)]
        ag = sum(gains[:14]) / 14
        al = sum(losses[:14]) / 14
        for i in range(15, n):
            ag = (ag * 13 + gains[i - 1]) / 14
            al = (al * 13 + losses[i - 1]) / 14
            rsi[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)

    def sma(i, w):
        return sum(c[i - w + 1:i + 1]) / w if i - w + 1 >= 0 else None

    out = []
    for i in range(n):
        f = {}
        if rsi[i] is not None:
            f["rsi14"] = rsi[i]
        s50, s200 = sma(i, 50), sma(i, 200)
        if s50:
            f["dist_sma50"] = c[i] / s50 - 1
        if s200:
            f["dist_sma200"] = c[i] / s200 - 1
        if i >= 252:
            f["mom_12_1"] = c[i - 21] / c[i - 252] - 1
            hi252 = max(h[i - 251:i + 1])
            f["dist_52w_high"] = c[i] / hi252 - 1
            f["drawdown_252"] = c[i] / max(c[i - 251:i + 1]) - 1
        if i >= 20:
            rets = [c[j] / c[j - 1] - 1 for j in range(i - 19, i + 1)]
            mu = sum(rets) / len(rets)
            f["vol_20d_annual"] = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets)) * math.sqrt(252)
        if f:
            out.append((dates[i], f))
    return out


# --- fundamental TTM from FMP (stamped at filingDate) ---------------------------
def _fundamental(symbol):
    stmts = fmp.income_statement(symbol, period="quarter", limit=80)
    stmts = sorted([s for s in stmts if s.get("date")], key=lambda s: s["date"])  # oldest->newest
    out = []
    for i in range(3, len(stmts)):
        win = stmts[i - 3:i + 1]                    # trailing 4 quarters
        rev_ttm = sum((s.get("revenue") or 0) for s in win)
        ni_ttm = sum((s.get("netIncome") or 0) for s in win)
        eps_ttm = sum((s.get("eps") or 0) for s in win)
        f = {"revenue_ttm": rev_ttm or None, "netincome_ttm": ni_ttm,
             "eps_ttm": eps_ttm or None,
             "net_margin_ttm": (ni_ttm / rev_ttm) if rev_ttm else None}
        if i >= 7:                                  # YoY needs 8 quarters
            prev = sum((s.get("revenue") or 0) for s in stmts[i - 7:i - 3])
            f["revenue_growth_yoy"] = (rev_ttm / prev - 1) if prev else None
        knowable = stmts[i].get("filingDate") or stmts[i].get("acceptedDate") or stmts[i]["date"]
        out.append((knowable[:10], f))
    return out


def _earnings_surprise(symbol):
    out = []
    for e in fmp.earnings(symbol, limit=120):
        a, est, d = e.get("epsActual"), e.get("epsEstimated"), e.get("date")
        if a is None or est is None or not d:
            continue
        denom = abs(est) if abs(est) > 0.01 else 0.01
        out.append((d[:10], {"eps_surprise_pct": (a - est) / denom}))
    return out


# --- survivorship-free universe -------------------------------------------------
def members_asof(target_date: str) -> set[str]:
    """S&P 500 membership as of target_date, reconstructed by replaying changes backward."""
    cur = {c["symbol"] for c in fmp.sp500_current() if c.get("symbol")}
    for ch in fmp.sp500_historical_changes():       # each: date, symbol(added), removedTicker
        d = ch.get("date")
        if not d or d <= target_date:
            continue                                 # change already reflected at/ before target
        added, removed = ch.get("symbol"), ch.get("removedTicker")
        if added:
            cur.discard(added)                       # wasn't a member before it was added
        if removed:
            cur.add(removed)                         # was a member before it was removed
    return cur


def _build_one(conn, sym, days):
    """Compute + store all features for one ticker. Prices/technicals always; fundamentals best-effort
    (delisted names often lack them but prices are still useful). Returns rows written."""
    rows = []
    bars = _prices(sym, days)
    for d, f in _technical(bars):
        _emit(rows, sym, d, d, "price", f)
    try:
        for d, f in _fundamental(sym):
            _emit(rows, sym, d, d, "fmp", f)
        for d, f in _earnings_surprise(sym):
            _emit(rows, sym, d, d, "fmp", f)
    except Exception:
        pass
    for fn, src in ((_insider, "fmp"), (_revisions, "fmp"), (_sector, "sector"), (_news, "news"),
                    (_short_interest, "massive")):
        try:
            for d, f in fn(sym):
                _emit(rows, sym, d, d, src, f)
        except Exception:
            pass
    conn.executemany("INSERT OR REPLACE INTO features VALUES(?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def augment_news(top_n):
    """Backfill historical news features onto the most-liquid N names (news backfill is API-heavy)."""
    conn = _conn()
    syms = [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (top_n,))]
    ok = 0
    for i, s in enumerate(syms):
        rows = []
        try:
            for d, f in _news(s):
                _emit(rows, s, d, d, "news", f)
        except Exception as e:
            print(f"  {s} news FAIL {str(e)[:60]}", file=sys.stderr)
        if rows:
            conn.executemany("INSERT OR REPLACE INTO features VALUES(?,?,?,?,?,?)", rows)
            conn.commit()
            ok += 1
        if (i + 1) % 25 == 0:
            print(f"  news {i+1}/{len(syms)} (ok={ok})", flush=True)
    print(f"augment-news done: news features added to {ok}/{len(syms)} liquid names -> {OUT}", flush=True)
    conn.close()


def augment(days):
    """Backfill the Phase-A features (insider, analyst-revision, sector strength) onto every ticker
    already in the store — without redoing technical/fundamental. For adding features post-hoc."""
    conn = _conn()
    syms = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM features WHERE source='price'")]
    ok = 0
    for i, s in enumerate(syms):
        rows = []
        for fn, src in ((_insider, "fmp"), (_revisions, "fmp"), (_sector, "sector"),
                        (_short_interest, "massive")):
            try:
                for d, f in fn(s):
                    _emit(rows, s, d, d, src, f)
            except Exception:
                pass
        if rows:
            conn.executemany("INSERT OR REPLACE INTO features VALUES(?,?,?,?,?,?)", rows)
            conn.commit()
            ok += 1
        if (i + 1) % 100 == 0:
            print(f"  augment {i+1}/{len(syms)} (ok={ok})", flush=True)
        time.sleep(0.08)
    print(f"augment done: Phase-A features added to {ok}/{len(syms)} tickers -> {OUT}", flush=True)
    conn.close()


def build(universe, days):
    conn = _conn()
    total = 0
    for sym in universe:
        try:
            n = _build_one(conn, sym, days)
            print(f"  {sym}: +{n} feature rows")
            total += n
        except Exception as e:
            print(f"  {sym}: FAILED {str(e)[:70]}", file=sys.stderr)
        time.sleep(0.2)
    print(f"\ntotal feature rows written: {total} -> {OUT}")
    print("by source:", dict(conn.execute("SELECT source, COUNT(*) FROM features GROUP BY source").fetchall()))
    conn.close()


def build_universe(market_cap_min, cap_active, cap_delisted, days):
    """Assemble a BROAD survivorship-safe universe — all-cap active NASDAQ/NYSE (not just S&P)
    ∪ delisted names — and build features for it. Resumable: skips tickers already built, so a long
    background run can be re-invoked and continue."""
    conn = _conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS universe(symbol TEXT PRIMARY KEY, market_cap REAL,
        sector TEXT, status TEXT, ipo_date TEXT, delisted_date TEXT)""")
    active = [x for x in fmp.screener(market_cap_min)
              if x.get("symbol") and not (x.get("isEtf") or x.get("isFund"))]
    active.sort(key=lambda x: -(x.get("marketCap") or 0))
    active = active[:cap_active]
    delisted = []
    for pg in range(0, cap_delisted // 100 + 2):
        try:
            page = fmp.delisted_companies(pg)
        except Exception:
            break
        if not page:
            break
        delisted += [x for x in page if x.get("symbol") and x.get("exchange") in ("NASDAQ", "NYSE", "AMEX")]
    delisted = delisted[:cap_delisted]
    urows = ([(x["symbol"], x.get("marketCap"), x.get("sector"), "active", None, None) for x in active]
             + [(x["symbol"], None, x.get("sector"), "delisted", x.get("ipoDate"), x.get("delistedDate")) for x in delisted])
    conn.executemany("INSERT OR REPLACE INTO universe VALUES(?,?,?,?,?,?)", urows)
    conn.commit()

    built = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM features WHERE source='price'")}
    todo = [s for s in dict.fromkeys(r[0] for r in urows) if s not in built]
    print(f"UNIVERSE: {len(urows)} symbols ({len(active)} active + {len(delisted)} delisted) | "
          f"already built {len(built)} | to build {len(todo)}", flush=True)
    ok = fail = 0
    for i, sym in enumerate(todo):
        try:
            _build_one(conn, sym, days)
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  [{i+1}/{len(todo)}] {sym} FAIL {str(e)[:50]}", file=sys.stderr)
        if (i + 1) % 50 == 0:
            print(f"  progress {i+1}/{len(todo)}  ok={ok} fail={fail}", flush=True)
        time.sleep(0.15)
    nfeat = conn.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    ntick = conn.execute("SELECT COUNT(DISTINCT ticker) FROM features").fetchone()[0]
    print(f"\nDONE: built {ok}, failed {fail}. feature store now {ntick} tickers, {nfeat} rows -> {OUT}", flush=True)
    conn.close()


def refresh_live(top_n, extra, days):
    """Daily refresh: rebuild features for a bounded LIVE universe (most-liquid active names +
    any extras) with FRESH prices (clears their FMP price cache first). Fast enough for a cron step."""
    conn = _conn()
    syms = [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (top_n,))]
    syms = list(dict.fromkeys(syms + [s.strip().upper() for s in extra if s.strip()]))
    # bust the FMP price cache so prices are current
    import glob
    for s in syms:
        for f in glob.glob(os.path.expanduser(f"~/.openclaw/state/market-data-cache/fmp_historical-price-eod_full_*symbol-{s.lower()}*")) \
                 + glob.glob(os.path.expanduser(f"~/.openclaw/state/market-data-cache/alpaca_{s.lower()}_*")):
            try:
                os.remove(f)
            except OSError:
                pass
    ok = 0
    for s in syms:
        try:
            _build_one(conn, s, days)
            ok += 1
        except Exception as e:
            print(f"  {s} refresh FAIL {str(e)[:50]}", file=sys.stderr)
        time.sleep(0.1)
    print(f"refresh-live: refreshed {ok}/{len(syms)} live names with fresh prices -> {OUT}")
    conn.close()


def verify(ticker):
    conn = _conn()
    print(f"=== point-in-time check for {ticker} ===")
    # show fundamental knowable_at lags the fiscal period (no look-ahead)
    rows = conn.execute("SELECT as_of, name, value FROM features WHERE ticker=? AND name='revenue_ttm' "
                        "ORDER BY as_of DESC LIMIT 3", (ticker,)).fetchall()
    print("latest revenue_ttm (as_of = filingDate, i.e. when it became knowable):")
    for r in rows:
        print(f"   as_of={r[0]}  revenue_ttm={r[2]/1e9:.1f}B")
    # point-in-time read at a past date
    for d in ("2020-01-15", "2023-06-15"):
        rr = conn.execute("SELECT value, as_of FROM features WHERE ticker=? AND name='eps_ttm' AND as_of<=? "
                          "ORDER BY as_of DESC LIMIT 1", (ticker, d)).fetchone()
        rsi = conn.execute("SELECT value FROM features WHERE ticker=? AND name='rsi14' AND as_of<=? "
                           "ORDER BY as_of DESC LIMIT 1", (ticker, d)).fetchone()
        print(f"as-of {d}: eps_ttm={rr[0]:.2f} (filed {rr[1]})  rsi14={rsi[0]:.1f}" if rr and rsi else f"as-of {d}: n/a")
    es = conn.execute("SELECT COUNT(*) FROM features WHERE ticker=? AND name='eps_surprise_pct'", (ticker,)).fetchone()[0]
    print(f"earnings-surprise events stored: {es}")
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--universe", required=True); b.add_argument("--days", type=int, default=4000)
    u = sub.add_parser("build-universe")
    u.add_argument("--market-cap-min", type=float, default=1e9)
    u.add_argument("--cap-active", type=int, default=1200)
    u.add_argument("--cap-delisted", type=int, default=400)
    u.add_argument("--days", type=int, default=4000)
    rl = sub.add_parser("refresh-live")
    rl.add_argument("--top-n", type=int, default=150)
    rl.add_argument("--extra", default="")
    rl.add_argument("--days", type=int, default=4000)
    au = sub.add_parser("augment"); au.add_argument("--days", type=int, default=4000)
    an = sub.add_parser("augment-news"); an.add_argument("--top-n", type=int, default=150)
    m = sub.add_parser("members-asof"); m.add_argument("date")
    v = sub.add_parser("verify"); v.add_argument("ticker")
    a = ap.parse_args()
    if a.cmd == "build":
        build([s.strip().upper() for s in a.universe.split(",") if s.strip()], a.days)
    elif a.cmd == "build-universe":
        build_universe(a.market_cap_min, a.cap_active, a.cap_delisted, a.days)
    elif a.cmd == "refresh-live":
        refresh_live(a.top_n, a.extra.split(",") if a.extra else [], a.days)
    elif a.cmd == "augment":
        augment(a.days)
    elif a.cmd == "augment-news":
        augment_news(a.top_n)
    elif a.cmd == "members-asof":
        mem = members_asof(a.date)
        cur = {c["symbol"] for c in fmp.sp500_current()}
        gone = sorted(mem - cur)
        print(f"S&P 500 members as-of {a.date}: {len(mem)} names")
        print(f"  of those, {len(gone)} are NO LONGER in today's index (removed/delisted/acquired):")
        print("   ", ", ".join(gone[:40]) + (" ..." if len(gone) > 40 else ""))
    elif a.cmd == "verify":
        verify(a.ticker)


if __name__ == "__main__":
    main()
