#!/usr/bin/env python3
"""Factor-regime detector — momentum-vs-value leadership + the desk's factor concentration.

The macro regime layer (`classify_regime`) is a risk-on/off detector: it reads SPY/credit/
VIX/curve and is blind to intra-market FACTOR rotations. The desk's 2026-06-12→07-21 losses
came from being monolithically momentum/growth-tilted into a momentum unwind the macro layer
never saw (SPY was ~flat). Empirically, over that window the desk's momentum theses returned
-6.3% and growth -5.5% (market-relative), while MTUM underperformed VLUE by ~1.4pp and SPY by
~4pp — a rotation clearly visible in factor-proxy ETFs.

This computes, deterministically from factor ETFs (Massive) + the desk store:
  * market factor leadership   — momentum (MTUM) vs value (VLUE) trailing relative return,
  * the desk's factor concentration — open positions bucketed by linked-mechanism family,
and flags when the desk is over-concentrated in a factor the market is currently punishing.

MEASUREMENT ONLY: read-only, no schema change, no trading behavior. It prints a JSON report
(and is safe to run as a pass stage / feed the telemetry). Any origination/sizing change that
CONSUMES this — e.g. a `factor_fit` down-weight in signal_scan — ships as a gated rule_proposal.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors import massive  # noqa: E402  (market-data backbone; Alpaca-free)

DB_PATH = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
MOMENTUM_ETF = "MTUM"
VALUE_ETF = "VLUE"
BENCH_ETF = "SPY"
# leadership thresholds on the 21d momentum-minus-value spread (percentage points)
LEAD_THRESHOLD = 1.0
# concentration alarm: a single factor family this share of gross deployment is "over-concentrated"
CONCENTRATION_ALARM = 0.50


def factor_family(mechanism_id: str) -> str:
    """Bucket a calibrated mechanism into a factor family (validated against realized outcomes)."""
    m = (mechanism_id or "").lower()
    if "mom_12_1" in m or "trend" in m or "momentum" in m:
        return "momentum"
    if "revenue_growth" in m or "growth" in m:
        return "growth"
    if "drawdown" in m or "oversold" in m or "revers" in m or "rsi" in m:
        return "mean_rev"
    if "pe" in m or "value" in m or "cheap" in m:
        return "value"
    if "vix" in m or "vol" in m:
        return "vol"
    if "days_to_cover" in m or "short" in m or "squeeze" in m:
        return "short_sq"
    if "sentiment" in m or "news" in m:
        return "sentiment"
    if "rating" in m or "upgrade" in m:
        return "rating"
    return "other"


# momentum/growth are the pro-cyclical high-beta cluster the unwind punished; value/mean_rev the defensive.
PROCYCLICAL = {"momentum", "growth"}


def _closes(sym: str) -> dict[str, float]:
    return {b["t"]: float(b["c"]) for b in massive.daily_bars(sym) if b.get("c") is not None}


def _trailing_return(series: dict[str, float], as_of: str, lookback_td: int) -> float | None:
    dates = sorted(d for d in series if d <= as_of)
    if len(dates) <= lookback_td:
        return None
    return (series[dates[-1]] / series[dates[-1 - lookback_td]] - 1.0) * 100.0


def market_leadership(as_of: str | None = None) -> dict:
    """Momentum(MTUM)-vs-value(VLUE) leadership from trailing relative return."""
    mt, vl, sp = _closes(MOMENTUM_ETF), _closes(VALUE_ETF), _closes(BENCH_ETF)
    as_of = as_of or max(mt)
    out: dict = {"as_of": as_of}
    for lb, tag in ((21, "21d"), (63, "63d")):
        m, v, s = (_trailing_return(mt, as_of, lb), _trailing_return(vl, as_of, lb),
                   _trailing_return(sp, as_of, lb))
        out[f"mom_ret_{tag}"] = None if m is None else round(m, 2)
        out[f"val_ret_{tag}"] = None if v is None else round(v, 2)
        out[f"mom_minus_val_{tag}"] = None if (m is None or v is None) else round(m - v, 2)
        out[f"mom_minus_spy_{tag}"] = None if (m is None or s is None) else round(m - s, 2)
    spread = out.get("mom_minus_val_21d")
    if spread is None:
        out["leadership"] = "unknown"
    elif spread <= -LEAD_THRESHOLD:
        out["leadership"] = "value_leading"      # momentum being punished
    elif spread >= LEAD_THRESHOLD:
        out["leadership"] = "momentum_leading"
    else:
        out["leadership"] = "balanced"
    return out


def origination_tilt(conn: sqlite3.Connection, days: int = 30) -> dict:
    """Factor-family tilt of what the desk has been PROPOSING (recent predictions).

    Measured from origination rather than the held snapshot: the tilt problem lives in what the
    desk keeps proposing, and recent predictions carry mechanism linkage (most held positions are
    broker-synced placeholders with none). A prediction can touch several families; each linked
    family gets a count.
    """
    rows = conn.execute(
        "SELECT mechanism_ids_json FROM predictions "
        "WHERE predicted_at >= datetime('now', ?) AND mechanism_ids_json IS NOT NULL",
        (f"-{days} days",),
    ).fetchall()
    by_family: dict[str, int] = {}
    linked = 0
    for r in rows:
        try:
            mechs = json.loads(r["mechanism_ids_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            mechs = []
        fams = {factor_family(m.get("id") if isinstance(m, dict) else m) for m in (mechs or [])}
        fams.discard("other")
        if fams:
            linked += 1
        for f in fams:
            by_family[f] = by_family.get(f, 0) + 1
    total = sum(by_family.values())
    shares = {f: round(v / total, 4) for f, v in by_family.items()} if total else {}
    procyclical_share = round(sum(shares.get(f, 0.0) for f in PROCYCLICAL), 4)
    top_family, top_share = (max(shares.items(), key=lambda kv: kv[1]) if shares else (None, 0.0))
    return {
        "window_days": days,
        "predictions_considered": len(rows),
        "predictions_with_linked_family": linked,
        "family_counts": dict(sorted(by_family.items(), key=lambda kv: -kv[1])),
        "family_share": dict(sorted(shares.items(), key=lambda kv: -kv[1])),
        "procyclical_share": procyclical_share,   # momentum+growth as a share of linked families
        "top_family": top_family,
        "top_share": round(top_share, 4),
    }


def snapshot(conn: sqlite3.Connection) -> dict:
    market = market_leadership()
    tilt = origination_tilt(conn)
    procyc = tilt.get("procyclical_share", 0.0)
    punished = market.get("leadership") == "value_leading"
    alarm = bool(punished and procyc >= CONCENTRATION_ALARM)
    read = (
        f"origination {procyc*100:.0f}% pro-cyclical (momentum+growth) while market leadership="
        f"{market.get('leadership')} (MTUM-VLUE 21d {market.get('mom_minus_val_21d')}pp)"
        + ("  ⚠ tilted into a punished factor" if alarm else "")
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market_leadership": market,
        "origination_tilt": tilt,
        "tilted_into_punished_factor": alarm,
        "read": read,
    }


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        print(json.dumps(snapshot(conn), indent=1, default=str))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
