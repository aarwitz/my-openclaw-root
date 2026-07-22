#!/usr/bin/env python3
"""Deterministic valuation engine (trading-intel).

The desk reasons brilliantly about *catalysts* (the world model) but had no notion
of what a company is *worth*. This script is the missing intrinsic-value layer:
a determinism-first valuation that the LLM agents reference but never invent.

For a single-name US equity it computes, from real data only:
  * an FCFF **DCF** fair value (real FCF, revenue-CAGR growth fading to a terminal
    rate, WACC from CAPM beta vs SPY and the FRED 10y risk-free),
  * a **reverse-DCF** market-implied growth ("what is the price assuming?"),
  * a transparent **earnings-multiple** cross-check (growth-justified P/E),
  * current diagnostic **multiples** (P/E, EV/Sales, EV/EBITDA, P/FCF),
  * a blended **fair value**, **margin of safety**, valuation **zone**, and a
    data-driven **confidence**.

Pure stdlib (matches worldmodel.py). Fails *soft*: anything it can't value (ETFs,
negative-FCF names, missing fundamentals) comes back `applicable=False` with a
reason, so the pipeline degrades to its prior behavior rather than breaking.

Data sources: SEC EDGAR (fundamentals), Yahoo (price + realized vol + beta),
FRED (risk-free). All free, keyless, cached.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import edgar, fred, massive, yahoo  # noqa: E402
from connectors._http import ConnectorError  # noqa: E402

# Tunables (could later flow through rule_proposals like HORIZON_PROFILE).
EQUITY_RISK_PREMIUM = 0.045
TERMINAL_GROWTH = 0.025
STAGE1_YEARS = 7      # explicit high-growth phase (compounders need a long runway)
FADE_YEARS = 3        # then fade to terminal — a 10y two-stage FCFF DCF
WACC_MIN, WACC_MAX = 0.07, 0.13
GROWTH_MIN, GROWTH_MAX = 0.0, 0.22
RF_DEFAULT = 0.043
MOS_CLAMP = 0.60  # single-stock DCF is noisy; bound the usable signal hard.


def _daily_log_returns(closes: list[float]) -> list[float]:
    out = []
    for a, b in zip(closes, closes[1:]):
        if a and b and a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def price_history(ticker: str, days: int = 260) -> list[float]:
    """Daily closes (oldest first). Massive first (the price backbone), then Yahoo,
    then yfinance — Yahoo aggressively blocks stdlib/curl on this host."""
    try:
        bars = massive.daily_bars(ticker)[-days:]
        cl = [float(b["c"]) for b in bars if b.get("c") is not None]
        if len(cl) >= 30:
            return cl
    except Exception:
        pass
    try:
        cl = yahoo.closes(ticker, "1y")
        if len(cl) >= 30:
            return cl
    except Exception:
        pass
    try:
        import yfinance as yf  # noqa: PLC0415
        h = yf.Ticker(ticker).history(period="1y")
        cl = [float(x) for x in h["Close"].tolist() if x == x]  # drop NaN
        if len(cl) >= 30:
            return cl
    except Exception:
        pass
    return []


def realized_vol_annual(closes: list[float]) -> float | None:
    r = _daily_log_returns(closes)
    if len(r) < 30:
        return None
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var) * math.sqrt(252)


def beta_vs_spy(stock: list[float], spy: list[float]) -> float | None:
    rs, rm = _daily_log_returns(stock), _daily_log_returns(spy)
    n = min(len(rs), len(rm))
    if n < 60:
        return None
    rs, rm = rs[-n:], rm[-n:]
    mm = sum(rm) / n
    ms = sum(rs) / n
    cov = sum((rm[i] - mm) * (rs[i] - ms) for i in range(n)) / (n - 1)
    var = sum((x - mm) ** 2 for x in rm) / (n - 1)
    if var <= 0:
        return None
    return cov / var


def revenue_cagr(series: dict) -> float | None:
    """Compound annual revenue growth from the EDGAR annual revenue series."""
    if not series:
        return None
    yrs = sorted(int(y) for y in series.keys())
    if len(yrs) < 2:
        return None
    first, last = yrs[0], yrs[-1]
    # keys may be int or str depending on json round-trips
    v0 = series.get(first, series.get(str(first)))
    v1 = series.get(last, series.get(str(last)))
    n = last - first
    if not v0 or not v1 or v0 <= 0 or v1 <= 0 or n <= 0:
        return None
    return (v1 / v0) ** (1.0 / n) - 1.0


def _dcf(fcf0: float, growth: float, wacc: float, net_debt: float, shares: float,
         terminal_g: float = TERMINAL_GROWTH) -> float | None:
    """Two-stage FCFF DCF → fair value per share. Stage 1: STAGE1_YEARS of constant
    `growth`. Stage 2: FADE_YEARS fading to terminal_g. Then a Gordon terminal."""
    if fcf0 is None or fcf0 <= 0 or shares is None or shares <= 0 or wacc <= terminal_g:
        return None
    pv = 0.0
    fcf = fcf0
    t = 0
    for _ in range(STAGE1_YEARS):
        t += 1
        fcf *= (1 + growth)
        pv += fcf / (1 + wacc) ** t
    for i in range(1, FADE_YEARS + 1):
        t += 1
        g_t = growth + (terminal_g - growth) * i / FADE_YEARS
        fcf *= (1 + g_t)
        pv += fcf / (1 + wacc) ** t
    terminal = fcf * (1 + terminal_g) / (wacc - terminal_g)
    pv += terminal / (1 + wacc) ** t
    equity_value = pv - (net_debt or 0.0)
    return equity_value / shares


def _reverse_dcf_growth(price: float, fcf0: float, wacc: float, net_debt: float, shares: float) -> float | None:
    """Solve for the constant near-term growth the current price implies (bisection)."""
    if not (price and fcf0 and fcf0 > 0 and shares and shares > 0) or wacc <= TERMINAL_GROWTH:
        return None
    lo, hi = -0.25, 0.80
    f = lambda g: (_dcf(fcf0, g, wacc, net_debt, shares) or 0.0) - price
    flo, fhi = f(lo), f(hi)
    if flo > 0 and fhi > 0:
        return lo  # price below even the bear case → implied growth at/under floor
    if flo < 0 and fhi < 0:
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < 1e-6:
            return mid
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def _risk_free() -> float:
    try:
        series = fred.fetch_series("DGS10")
        if series:
            return float(series[-1][1]) / 100.0
    except Exception:
        pass
    return RF_DEFAULT


def value(ticker: str) -> dict[str, Any]:
    t = ticker.upper().strip()
    out: dict[str, Any] = {"ticker": t, "applicable": False}

    # price + vol + beta (works for almost everything, incl. ETFs)
    closes = price_history(t)
    price = closes[-1] if closes else None
    rvol = realized_vol_annual(closes) if closes else None
    spy = price_history("SPY")
    beta = beta_vs_spy(closes, spy) if (closes and spy) else None
    out["price"] = round(price, 2) if price else None
    out["realized_vol_annual"] = round(rvol, 4) if rvol else None
    out["beta"] = round(beta, 3) if beta else None

    # fundamentals (single-name equities only)
    try:
        f = edgar.fundamentals(t)
    except ConnectorError as e:
        out["reason"] = str(e)
        return out

    shares = f.get("shares")
    fcf = f.get("fcf")
    net_debt = f.get("net_debt") or 0.0
    eps = f.get("eps_ttm")
    revenue = f.get("revenue")
    ebitda = f.get("ebitda")

    if not price or not shares:
        out["reason"] = "missing price or share count"
        return out

    rf = _risk_free()
    g = revenue_cagr(f.get("revenue_series") or {})
    g_used = max(GROWTH_MIN, min(GROWTH_MAX, g if g is not None else 0.05))
    wacc = max(WACC_MIN, min(WACC_MAX, rf + (beta if beta else 1.0) * EQUITY_RISK_PREMIUM))

    fair_dcf = _dcf(fcf, g_used, wacc, net_debt, shares)
    implied_growth = _reverse_dcf_growth(price, fcf, wacc, net_debt, shares)

    # current diagnostic multiples
    mktcap = price * shares
    ev = mktcap + net_debt
    pe_cur = (price / eps) if (eps and eps > 0) else None
    pfcf_cur = (mktcap / fcf) if (fcf and fcf > 0) else None
    multiples = {
        "pe": round(pe_cur, 2) if pe_cur else None,
        "ev_sales": round(ev / revenue, 2) if (revenue and revenue > 0) else None,
        "ev_ebitda": round(ev / ebitda, 2) if (ebitda and ebitda > 0) else None,
        "p_fcf": round(pfcf_cur, 2) if pfcf_cur else None,
    }

    # growth-justified earnings-multiple cross-check (Graham-style, transparent).
    # Skip it when GAAP earnings are distorted (P/E far above P/FCF, e.g. AVGO's
    # post-VMware acquisition amortization) — there, lean on the FCF-based DCF.
    fair_eps = None
    fair_pe = None
    earnings_clean = not (pe_cur and pfcf_cur and pe_cur > 2.5 * pfcf_cur)
    if eps and eps > 0 and earnings_clean:
        fair_pe = max(12.0, min(40.0, 11.0 + 1.1 * (g_used * 100)))
        fair_eps = fair_pe * eps

    # blend
    parts = []
    if fair_dcf and fair_dcf > 0:
        parts.append(("dcf", fair_dcf, 0.65))
    if fair_eps and fair_eps > 0:
        parts.append(("eps", fair_eps, 0.35))
    if not parts:
        out.update({"reason": "no positive-FCF DCF and no positive-EPS multiple",
                    "dcf_value": round(fair_dcf, 2) if fair_dcf else None,
                    "implied_growth": round(implied_growth, 4) if implied_growth is not None else None,
                    "multiples": multiples, "wacc": round(wacc, 4), "growth_assumed": round(g_used, 4)})
        return out
    wsum = sum(w for _, _, w in parts)
    fair_value = sum(v * w for _, v, w in parts) / wsum

    raw_mos = fair_value / price - 1.0
    mos = max(-MOS_CLAMP, min(MOS_CLAMP, raw_mos))  # the usable, bounded signal
    extreme = abs(raw_mos) > MOS_CLAMP
    if mos > 0.20:
        zone = "cheap"
    elif mos < -0.10:
        zone = "rich"
    else:
        zone = "fair"

    # confidence: rewards data completeness + method agreement, punishes blow-ups
    conf = 0.5
    if fair_dcf and fair_dcf > 0:
        conf += 0.20
    if fair_eps and fair_eps > 0:
        conf += 0.15
    if len(f.get("revenue_series") or {}) >= 3:
        conf += 0.15
    if fair_dcf and fair_eps and fair_dcf > 0 and fair_eps > 0:
        disp = abs(fair_dcf - fair_eps) / ((fair_dcf + fair_eps) / 2)
        conf -= min(0.35, disp * 0.35)
    if extreme:
        conf *= 0.4   # raw value is an extrapolation — don't trust the magnitude
    if abs(g_used - GROWTH_MAX) < 1e-9:
        conf *= 0.85  # growth pinned at the ceiling = high-growth extrapolation
    conf = max(0.1, min(0.95, conf))

    out.update({
        "applicable": True,
        "name": f.get("name"),
        # display a fair value consistent with the clamped MoS so an extreme raw
        # DCF can't surface an absurd headline price (raw kept in method_json).
        "fair_value": round(price * (1 + mos) if extreme else fair_value, 2),
        "fair_value_raw": round(fair_value, 2),
        "margin_of_safety": round(mos, 4),
        "raw_margin_of_safety": round(raw_mos, 4),
        "zone": zone,
        "confidence": round(conf, 3),
        "dcf_value": round(fair_dcf, 2) if fair_dcf else None,
        "eps_multiple_value": round(fair_eps, 2) if fair_eps else None,
        "fair_pe": round(fair_pe, 1) if fair_pe else None,
        "implied_growth": round(implied_growth, 4) if implied_growth is not None else None,
        "growth_assumed": round(g_used, 4),
        "wacc": round(wacc, 4),
        "risk_free": round(rf, 4),
        "multiples": multiples,
        "fundamentals": {k: f.get(k) for k in ("revenue", "fcf", "net_income", "ebitda",
                                               "shares", "net_debt", "eps_ttm")},
        "source": "edgar+yahoo+fred",
        "as_of": edgar.now_iso(),
    })
    return out


# ---------------------------------------------------------------------------
# Persistence + universe runner (so the deterministic pass can populate the DB)
# ---------------------------------------------------------------------------
import json  # noqa: E402
import sqlite3  # noqa: E402
import uuid  # noqa: E402

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def write_valuation(conn: sqlite3.Connection, v: dict, experiment_id: str | None = None) -> str:
    mults = v.get("multiples") or {}
    vid = f"val_{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO valuations (id, ticker, as_of, applicable, price, fair_value, "
        "margin_of_safety, zone, confidence, dcf_value, eps_multiple_value, implied_growth, "
        "growth_assumed, wacc, beta, realized_vol_annual, pe, ev_sales, ev_ebitda, p_fcf, "
        "reason, method_json, created_at, experiment_id) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            vid, v["ticker"], v.get("as_of") or edgar.now_iso(),
            1 if v.get("applicable") else 0, v.get("price"), v.get("fair_value"),
            v.get("margin_of_safety"), v.get("zone"), v.get("confidence"),
            v.get("dcf_value"), v.get("eps_multiple_value"), v.get("implied_growth"),
            v.get("growth_assumed"), v.get("wacc"), v.get("beta"), v.get("realized_vol_annual"),
            mults.get("pe"), mults.get("ev_sales"), mults.get("ev_ebitda"), mults.get("p_fcf"),
            v.get("reason"), json.dumps(v, default=str), edgar.now_iso(), experiment_id,
        ),
    )
    conn.commit()
    return vid


def latest_valuation(conn: sqlite3.Connection, ticker: str, max_age_h: float = 96.0) -> dict | None:
    """Most recent applicable valuation for a ticker, if fresh enough. Consumers
    (predict/score/critic) use this; returns None so they fall back gracefully."""
    row = conn.execute(
        "SELECT * FROM valuations WHERE ticker = ? AND applicable = 1 "
        "ORDER BY as_of DESC LIMIT 1", (ticker.upper(),),
    ).fetchone()
    if not row:
        return None
    from datetime import datetime, timezone
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(
            row["as_of"].replace("Z", "+00:00"))).total_seconds() / 3600.0
        if age_h > max_age_h:
            return None
    except (ValueError, AttributeError):
        pass
    return dict(row)


def universe_tickers(conn: sqlite3.Connection) -> list[str]:
    """Held names + tickers on live/scored hypotheses (de-duped)."""
    out: set[str] = set()
    try:
        for r in conn.execute("SELECT DISTINCT ticker FROM positions WHERE state='open'"):
            if r[0]:
                out.add(str(r[0]).upper())
    except sqlite3.Error:
        pass
    try:
        for r in conn.execute("SELECT tickers FROM hypotheses WHERE state IN "
                              "('active','ready','scored','challenged')"):
            try:
                for t in json.loads(r[0] or "[]"):
                    if t:
                        out.add(str(t).upper())
            except (json.JSONDecodeError, TypeError):
                continue
    except sqlite3.Error:
        pass
    return sorted(out)


def value_universe(tickers: list[str] | None = None, experiment_id: str | None = None) -> dict:
    conn = _conn()
    syms = tickers or universe_tickers(conn)
    rows, applic, na = [], 0, 0
    for t in syms:
        try:
            v = value(t)
        except Exception as e:  # never let one ticker break the pass
            v = {"ticker": t, "applicable": False, "reason": f"error: {e}"}
        write_valuation(conn, v, experiment_id)
        if v.get("applicable"):
            applic += 1
            rows.append(f"{t} MoS {v['margin_of_safety']*100:+.0f}% {v['zone']}")
        else:
            na += 1
    conn.close()
    return {"valued": applic, "n_a": na, "tickers": len(syms), "summary": rows}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "one"
    if cmd == "universe":
        extra = sys.argv[2:]
        print(json.dumps(value_universe(extra or None), indent=2, default=str))
    elif cmd == "one":
        sym = sys.argv[2] if len(sys.argv) > 2 else "AAPL"
        print(json.dumps(value(sym), indent=2, default=str))
    else:
        # back-compat: `valuation.py AAPL`
        print(json.dumps(value(cmd), indent=2, default=str))

