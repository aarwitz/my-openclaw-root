#!/usr/bin/env python3
"""Macro-regime brief for the LLM layer — the structured context that lets the agents APPLY the
regime-dependent chains (rate->duration, risk-off) by judgment when the regime warrants, while the
validated mechanical mechanisms (VIX-capitulation, mean-reversion, value, momentum, …) trade on their own.

Deterministic: pulls current macro state from FRED (rate level/trend, real yields, curve, credit
spreads, VIX), classifies the regime, and lists which regime-dependent PLAYBOOKS are active. The
backtest showed rate->duration is NOT a mechanical edge (regime-dependent) — so it lives HERE, gated
on the regime, not as a blind quant rule.

Writes state/regime_brief.json + prints a readable summary.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import fred  # noqa: E402

OUT = os.path.expanduser("~/.openclaw/state/regime_brief.json")


def _series(sid):
    try:
        return fred.fetch_series(sid)
    except Exception:
        return []


def _latest_and_chg(sid, win=63):
    s = _series(sid)
    if not s:
        return None, None
    latest = s[-1][1]
    chg = (s[-1][1] - s[-1 - win][1]) if len(s) > win else None
    return latest, chg


def main():
    rate, rate_chg = _latest_and_chg("DGS10")          # 10y nominal yield, 3mo change
    real, real_chg = _latest_and_chg("DFII10")         # 10y real yield
    curve, _ = _latest_and_chg("T10Y2Y")               # 10y-2y spread
    hy, hy_chg = _latest_and_chg("BAMLH0A0HYM2")        # HY OAS (credit stress)
    vix, _ = _latest_and_chg("VIXCLS")

    rates_dir = "stable"
    if rate_chg is not None:
        rates_dir = "rising" if rate_chg > 0.20 else ("falling" if rate_chg < -0.20 else "stable")
    rates_dominant = rate_chg is not None and abs(rate_chg) > 0.40
    risk_off = (vix is not None and vix > 25) or (hy_chg is not None and hy_chg > 1.0) or (hy is not None and hy > 6.0)
    risk_on = (vix is not None and vix < 15) and (hy is not None and hy < 3.5)
    inverted = curve is not None and curve < 0

    if risk_off:
        regime = "risk_off"
    elif rates_dominant:
        regime = "rates_dominant_" + (rates_dir if rates_dir != "stable" else "volatile")
    elif risk_on:
        regime = "risk_on"
    else:
        regime = "neutral_narrative_driven"

    playbooks = []
    if rates_dir == "rising":
        playbooks.append("DURATION HEADWIND: rates rising -> high-P/E/long-duration tech faces PV repricing; "
                         "require a stronger idiosyncratic catalyst before going long high-multiple names; the "
                         "rate->duration SHORT thesis is in play (esp. if rates_dominant).")
    if rates_dir == "falling":
        playbooks.append("DURATION TAILWIND: rates falling -> high-multiple growth tends to re-rate up; lean into "
                         "quality growth.")
    if risk_off:
        playbooks.append("RISK-OFF: credit widening / VIX elevated -> fade high-beta, favor quality/defensives; "
                         "BUT the VALIDATED VIX-capitulation mechanism still mechanically BUYS deep-drawdown names "
                         "at peak fear — distinguish 'falling knife' from 'capitulation bounce' by fundamentals.")
    if inverted:
        playbooks.append("CURVE INVERTED: late-cycle signal; weight balance-sheet quality.")
    if regime == "neutral_narrative_driven":
        playbooks.append("RATES NOT DOMINANT: do NOT over-apply the rate->tech chain (it loses in narrative-driven "
                         "tape); weight idiosyncratic fundamentals, catalysts, and the validated mechanical edges.")

    brief = {
        "generated": datetime.now(timezone.utc).isoformat() + "Z",
        "regime": regime,
        "macro": {"rate_10y": rate, "rate_10y_chg_63d": round(rate_chg, 2) if rate_chg is not None else None,
                  "real_yield_10y": real, "real_yield_chg_63d": round(real_chg, 2) if real_chg is not None else None,
                  "yield_curve_10y2y": curve, "hy_oas": hy,
                  "hy_oas_chg_63d": round(hy_chg, 2) if hy_chg is not None else None, "vix": vix},
        "rates_direction": rates_dir, "rates_dominant": rates_dominant, "risk_off": risk_off,
        "active_playbooks": playbooks,
    }
    json.dump(brief, open(OUT, "w"), indent=2)
    print(f"MACRO REGIME: {regime}")
    print(f"  10y={rate} (Δ63d {brief['macro']['rate_10y_chg_63d']}) | real={real} | curve={curve} | "
          f"HY_OAS={hy} (Δ {brief['macro']['hy_oas_chg_63d']}) | VIX={vix}")
    print(f"  rates={rates_dir} | rates_dominant={rates_dominant} | risk_off={risk_off} | inverted={inverted}")
    print("  active playbooks:")
    for p in playbooks:
        print("   -", p)
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
