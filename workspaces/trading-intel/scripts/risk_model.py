#!/usr/bin/env python3
"""Deterministic portfolio risk model (trading-intel).

The risk gate enforced *rule-based caps* (per-name %, gross %, name count, drawdown,
regime) but was blind to **correlation**: you could hold eight names that are all the
same AI-beta bet and every cap was satisfied. This module adds the covariance/factor
view the gate was missing — pure stdlib (no numpy), matching worldmodel.py.

What it computes from daily returns (Alpaca bars, cached):
  * the holdings **correlation matrix** and **correlation clusters** (the "same bet"
    detector — connected components at corr >= CORR_THRESHOLD),
  * portfolio **annualized volatility**, parametric **1-day VaR/CVaR**,
  * per-name **risk contributions** (Euler/MCR) and the **effective number of bets**
    (1 / HHI of risk shares) — 8 names that are 1 bet score ~1,
  * **factor betas** — univariate portfolio beta to a basket of factor-proxy ETFs
    (market/tech/small-cap/momentum/semis/energy/rates/gold): interpretable tilts.

Two entry points:
  * `snapshot()` — compute the whole picture for current holdings, write a
    `portfolio_risk` row, print it. Runs as a pass stage.
  * `correlated_cluster(conn, ticker, equity)` — used by the risk gate to cap a
    correlated cluster's combined exposure. Returns None on any data gap so the gate
    degrades to its prior behavior (never loosens an existing cap).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import alpaca  # noqa: E402
from connectors._http import ConnectorError  # noqa: E402

CORR_THRESHOLD = 0.70       # pairwise corr at/above this = "same bet"
MAX_CLUSTER_PCT = 0.25      # cluster combined exposure cap (gate)
RET_DAYS = 130              # ~6 months of trading days for the correlation window
MIN_OBS = 40               # need at least this many overlapping returns
TRADING_DAYS = 252
Z95, Z99 = 1.6448536, 2.3263479
CVAR95_FACTOR = 2.0627      # E[loss | loss > VaR95] for a normal = phi(z95)/0.05

# Factor-proxy ETFs (returns-based exposure; univariate betas avoid collinearity).
FACTOR_ETFS = {
    "market": "SPY", "tech": "QQQ", "smallcap": "IWM", "momentum": "MTUM",
    "semis": "SOXX", "energy": "XLE", "rates_long": "TLT", "gold": "GLD",
}

OPEN_POSITION_STATES = ("opening", "open", "scaling", "trimming", "closing")
PENDING_INTENT_STATES = ("approved", "submitted", "partial")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- math (pure stdlib) -----------------------------------------------------
def _log_returns(closes: list[float]) -> list[float]:
    out = []
    for a, b in zip(closes, closes[1:]):
        if a and b and a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _cov(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx, my = _mean(xs), _mean(ys)
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)


def _corr(xs: list[float], ys: list[float]) -> float:
    vx, vy = _cov(xs, xs), _cov(ys, ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return max(-1.0, min(1.0, _cov(xs, ys) / math.sqrt(vx * vy)))


def _returns_for(tickers: list[str], days: int = RET_DAYS) -> dict[str, list[float]]:
    """ticker -> daily log returns (most recent `days`), dropping names without data."""
    out: dict[str, list[float]] = {}
    for t in tickers:
        try:
            bars = alpaca.daily_bars(t, days=days)
            closes = [float(b["c"]) for b in bars if b.get("c") is not None]
            r = _log_returns(closes)
            if len(r) >= MIN_OBS:
                out[t] = r
        except Exception:
            continue
    return out


def _align(series: dict[str, list[float]]) -> tuple[list[str], list[list[float]]]:
    """Trim all return series to a common (most-recent) length."""
    if not series:
        return [], []
    n = min(len(v) for v in series.values())
    names = sorted(series)
    return names, [series[k][-n:] for k in names]


# --- DB exposure helpers (self-contained; mirror the gate's accounting) ------
def _name_exposure(conn, ticker: str) -> float:
    sym = (ticker or "").upper()
    total = 0.0
    for r in conn.execute(
        f"SELECT current_value, qty, cost_basis FROM positions WHERE UPPER(ticker)=? "
        f"AND state IN ({','.join('?' * len(OPEN_POSITION_STATES))})",
        (sym, *OPEN_POSITION_STATES),
    ):
        val = r["current_value"]
        if val is None:
            val = float(r["qty"] or 0) * float(r["cost_basis"] or 0)
        total += float(val or 0)
    for r in conn.execute(
        f"SELECT size, entry_price_target FROM trade_intents WHERE UPPER(ticker)=? "
        f"AND state IN ({','.join('?' * len(PENDING_INTENT_STATES))})",
        (sym, *PENDING_INTENT_STATES),
    ):
        total += float(r["size"] or 0) * float(r["entry_price_target"] or 0)
    return total


def _exposed_tickers(conn) -> list[str]:
    names: set[str] = set()
    for r in conn.execute(
        f"SELECT DISTINCT UPPER(ticker) t FROM positions "
        f"WHERE state IN ({','.join('?' * len(OPEN_POSITION_STATES))})", OPEN_POSITION_STATES):
        if r["t"]:
            names.add(r["t"])
    for r in conn.execute(
        f"SELECT DISTINCT UPPER(ticker) t FROM trade_intents "
        f"WHERE state IN ({','.join('?' * len(PENDING_INTENT_STATES))})", PENDING_INTENT_STATES):
        if r["t"]:
            names.add(r["t"])
    return sorted(names)


# --- gate hook: correlated cluster exposure ---------------------------------
def correlated_cluster(conn, ticker: str, equity: float) -> dict | None:
    """For a candidate `ticker`, the existing $ exposure of the cluster of holdings
    it is highly correlated with (incl. its own existing exposure). The gate caps
    cluster total at MAX_CLUSTER_PCT * equity. Returns None on any data gap so the
    gate falls back to its prior behavior (never loosens an existing cap)."""
    sym = (ticker or "").upper()
    holdings = [h for h in _exposed_tickers(conn) if h != sym]
    if not holdings:
        return {"members": [sym], "value": _name_exposure(conn, sym), "corrs": {}}
    series = _returns_for([sym] + holdings)
    if sym not in series:
        return None  # can't assess correlation for the candidate → skip the check
    members, corrs = [sym], {}
    for h in holdings:
        if h in series:
            c = _corr(series[sym], series[h])
            corrs[h] = round(c, 2)
            if c >= CORR_THRESHOLD:
                members.append(h)
    value = sum(_name_exposure(conn, m) for m in members)
    return {"members": members, "value": value, "corrs": corrs,
            "cap_pct": MAX_CLUSTER_PCT, "cap": MAX_CLUSTER_PCT * equity}


# --- full portfolio risk snapshot -------------------------------------------
def _clusters(names: list[str], series: list[list[float]]) -> list[list[str]]:
    n = len(names)
    adj = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if _corr(series[i], series[j]) >= CORR_THRESHOLD:
                adj[i].add(j)
                adj[j].add(i)
    seen, out = set(), []
    for i in range(n):
        if i in seen:
            continue
        stack, comp = [i], []
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            comp.append(k)
            stack.extend(adj[k] - seen)
        if len(comp) > 1:
            out.append(sorted(names[k] for k in comp))
    return out


def _desk_book(conn) -> tuple[dict[str, float], float | None]:
    """The LIVE desk book from the canonical store — the internal paper engine's
    book since the 2026-07-07 (D52) cutover.

    Before this, snapshot() read `alpaca.list_positions()`, but the D52 cutover
    removed Alpaca from the money path and nothing syncs desk fills to it, so the
    broker account is frozen at cutover (a stale ~24-name subset). Reading the
    canonical `positions` (+ `book_equity`) makes the risk/beta/exposure view
    reflect the real ~46-name desk book. Holdings are signed market values
    (shorts negative), matching the gate's own exposure accounting.
    """
    holdings: dict[str, float] = {}
    for r in conn.execute(
        f"SELECT UPPER(ticker) t, COALESCE(current_value, qty*cost_basis) v "
        f"FROM positions WHERE state IN ({','.join('?' * len(OPEN_POSITION_STATES))})",
        OPEN_POSITION_STATES,
    ):
        if r["t"] is None:
            continue
        holdings[r["t"]] = holdings.get(r["t"], 0.0) + float(r["v"] or 0.0)
    holdings = {t: v for t, v in holdings.items() if abs(v) > 0}

    eq_row = conn.execute(
        "SELECT equity FROM book_equity WHERE book='desk' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    equity = float(eq_row["equity"]) if eq_row and eq_row["equity"] is not None else None
    if equity is None:  # fall back to the capital-efficiency snapshot's equity
        ce = conn.execute(
            "SELECT equity FROM capital_efficiency_snapshots ORDER BY as_of DESC LIMIT 1"
        ).fetchone()
        equity = float(ce["equity"]) if ce and ce["equity"] is not None else None
    return holdings, equity


def snapshot(write: bool = True, experiment_id: str | None = None) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    holdings, equity = _desk_book(conn)
    if not equity:
        return {"error": "no desk equity available (book_equity/capital_efficiency both empty)"}

    if len(holdings) < 2:
        out = {"as_of": _now_iso(), "equity": round(equity, 2), "n_positions": len(holdings),
               "gross_exposure": round(sum(abs(v) for v in holdings.values()), 2),
               "note": "need >=2 positions for a covariance view"}
        if write:
            _write(conn, out, experiment_id)
        return out

    names = sorted(holdings)
    gross = sum(abs(v) for v in holdings.values())
    weights = {t: holdings[t] / gross for t in names}

    series_raw = _returns_for(names)
    names = [t for t in names if t in series_raw]  # keep only names with returns
    if len(names) < 2:
        return {"error": "insufficient return data for holdings"}
    _, aligned = _align({t: series_raw[t] for t in names})
    idx = {t: i for i, t in enumerate(names)}
    w = [weights[t] for t in names]
    wsum = sum(w)
    w = [x / wsum for x in w]  # renormalize over names we have data for

    # covariance (daily) and portfolio variance
    cov = [[_cov(aligned[i], aligned[j]) for j in range(len(names))] for i in range(len(names))]
    port_var_d = sum(w[i] * w[j] * cov[i][j] for i in range(len(names)) for j in range(len(names)))
    port_vol_d = math.sqrt(max(0.0, port_var_d))
    port_vol_ann = port_vol_d * math.sqrt(TRADING_DAYS)

    # risk contributions (Euler): CCR_i = w_i * (cov·w)_i / port_vol_d
    risk_contrib = {}
    if port_vol_d > 0:
        for i, t in enumerate(names):
            cw = sum(cov[i][j] * w[j] for j in range(len(names)))
            risk_contrib[t] = round((w[i] * cw / port_vol_d) / port_vol_d, 4)  # share of total risk
    shares = list(risk_contrib.values())
    eff_bets = round(1.0 / sum(s * s for s in shares), 2) if shares and sum(s * s for s in shares) > 0 else None

    # parametric VaR/CVaR on gross exposure
    var_1d_95 = round(Z95 * port_vol_d * gross, 2)
    var_1d_99 = round(Z99 * port_vol_d * gross, 2)
    cvar_1d_95 = round(CVAR95_FACTOR * port_vol_d * gross, 2)

    # factor betas (univariate): portfolio daily return vs each factor ETF
    port_ret = [sum(w[i] * aligned[i][t] for i in range(len(names))) for t in range(len(aligned[0]))]
    fac_series = _returns_for(list(FACTOR_ETFS.values()))
    factor_betas = {}
    for label, etf in FACTOR_ETFS.items():
        if etf in fac_series:
            fr = fac_series[etf]
            n = min(len(port_ret), len(fr))
            if n >= MIN_OBS:
                vf = _cov(fr[-n:], fr[-n:])
                if vf > 0:
                    factor_betas[label] = round(_cov(port_ret[-n:], fr[-n:]) / vf, 2)

    clusters = _clusters(names, aligned)
    cluster_detail = []
    for cl in clusters:
        val = sum(holdings[t] for t in cl)
        cluster_detail.append({"members": cl, "weight_pct": round(100 * val / gross, 1),
                               "equity_pct": round(100 * val / equity, 1) if equity else None})

    top_rc = sorted(risk_contrib.items(), key=lambda kv: -kv[1])[:5]
    modeled_gross = sum(abs(holdings[t]) for t in names)
    out = {
        "as_of": _now_iso(), "equity": round(equity, 2),
        "n_positions": len(holdings),                    # whole desk book
        "n_positions_modeled": len(names),               # covariance/beta-eligible subset (needs price history)
        "modeled_coverage_pct": round(100 * modeled_gross / gross, 1) if gross else None,
        "gross_exposure": round(gross, 2),               # whole desk book
        "gross_pct": round(100 * gross / equity, 1) if equity else None,
        "portfolio_vol_annual": round(port_vol_ann, 4),
        "var_1d_95": var_1d_95, "var_1d_99": var_1d_99, "cvar_1d_95": cvar_1d_95,
        "var_1d_95_pct": round(100 * var_1d_95 / equity, 2) if equity else None,
        "effective_bets": eff_bets,
        "factor_betas": factor_betas,
        "top_risk_contributors": [{"ticker": t, "risk_share_pct": round(100 * s, 1)} for t, s in top_rc],
        "clusters": cluster_detail,
        "source": "desk-book (canonical positions) + alpaca-bars",
    }
    if write:
        _write(conn, out, experiment_id)
    return out


def _write(conn, out: dict, experiment_id: str | None) -> None:
    conn.execute(
        "INSERT INTO portfolio_risk (id, as_of, equity, n_positions, gross_exposure, "
        "gross_pct, portfolio_vol_annual, var_1d_95, var_1d_99, cvar_1d_95, var_1d_95_pct, "
        "effective_bets, factor_betas_json, risk_contributions_json, clusters_json, "
        "method_json, created_at, experiment_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f"prisk_{uuid.uuid4().hex[:12]}", out.get("as_of"), out.get("equity"),
            out.get("n_positions"), out.get("gross_exposure"), out.get("gross_pct"),
            out.get("portfolio_vol_annual"), out.get("var_1d_95"), out.get("var_1d_99"),
            out.get("cvar_1d_95"), out.get("var_1d_95_pct"), out.get("effective_bets"),
            json.dumps(out.get("factor_betas")), json.dumps(out.get("top_risk_contributors")),
            json.dumps(out.get("clusters")), json.dumps(out, default=str), _now_iso(), experiment_id,
        ),
    )
    conn.commit()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    if cmd == "snapshot":
        print(json.dumps(snapshot(write=True), indent=2, default=str))
    elif cmd == "report":
        print(json.dumps(snapshot(write=False), indent=2, default=str))
    elif cmd == "cluster":
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        print(json.dumps(correlated_cluster(c, sys.argv[2], 100000.0), indent=2, default=str))
