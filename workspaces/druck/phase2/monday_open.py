"""Monday-open orchestrator (PHASE_II_PLAN.md §9 + MONDAY_OPEN_RUNBOOK.md).

Single command end-to-end:
  1. Refresh Schwab/Alpaca state (read cached Schwab + live Alpaca)
  2. Mirror-sync delta check
  3. Compute regime
  4. Generate candidates
  5. Normalize + score each (with Alpaca live confirmation on)
  6. Surface top actions
  7. (Optional) place Alpaca paper orders for buy_ready names within policy
  8. Log results
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Optional

from . import candidate_gen, normalize as norm, regime as regime_mod
from .adapters import alpaca, schwab, sheets
from .http_util import write_cache, now_iso
from .schema import RecommendationClass


# ---- Policy guardrails for autonomous paper execution ----
MAX_NEW_PAPER_NAMES_PER_DAY = 2
MAX_RISK_PER_NAME_PCT       = 2.0   # of NAV
MIN_DOLLAR_VOLUME_M         = 50.0  # only liquid names get auto-orders


@dataclass
class MondayReport:
    timestamp: str
    regime: str
    nav_usd: Optional[float]
    schwab_position_count: int
    alpaca_position_count: int
    alpaca_open_orders: int
    candidate_count: int
    top: list[dict]
    actions_taken: list[dict]
    warnings: list[str]


def _alpaca_safe_call(fn, default):
    try:
        return fn()
    except Exception as e:
        return e


def run(
    *,
    place_orders: bool = False,
    write_sheet: bool = True,
    seed: Optional[list[str]] = None,
    max_universe: int = 25,
) -> MondayReport:
    warnings: list[str] = []
    ts = now_iso()

    # 1. account state
    acct = _alpaca_safe_call(alpaca.account, None)
    nav: Optional[float] = None
    if isinstance(acct, dict):
        try:
            nav = float(acct.get("portfolio_value") or acct.get("equity") or 0)
        except Exception:
            nav = None
    else:
        warnings.append(f"alpaca.account failed: {acct}")

    a_pos = _alpaca_safe_call(alpaca.positions, [])
    if not isinstance(a_pos, list):
        warnings.append(f"alpaca.positions failed: {a_pos}")
        a_pos = []
    a_orders = _alpaca_safe_call(alpaca.open_orders, [])
    if not isinstance(a_orders, list):
        warnings.append(f"alpaca.open_orders failed: {a_orders}")
        a_orders = []

    s_tickers = schwab.position_tickers()
    a_tickers = sorted(set(p.get("symbol", "").upper() for p in a_pos if p.get("symbol")))
    mirror_drift = sorted(set(s_tickers) ^ set(a_tickers))
    if mirror_drift:
        warnings.append(f"mirror drift vs Schwab: {mirror_drift[:10]}")

    # 2. regime
    rg = regime_mod.compute()

    # 3. universe → score
    universe = candidate_gen.generate(seed=seed, max_tickers=max_universe)
    records = []
    for t in universe:
        try:
            r = norm.normalize(t, regime=rg, include_alpaca_live=True, nav_usd=nav)
            records.append(r)
        except Exception as e:
            warnings.append(f"normalize({t}): {e}")

    # 4. write to sheet
    if write_sheet:
        for r in records:
            try:
                sheets.upsert_candidate(r.as_dict())
            except Exception as e:
                warnings.append(f"sheet write {r.ticker}: {e}")

    # 5. surface top actions
    ranked = sorted(records, key=lambda r: r.total_score_final, reverse=True)
    top = [
        {
            "ticker": r.ticker, "class": r.recommendation_class,
            "score": r.total_score_final, "setup": r.setup_state,
            "catalyst": r.verified_catalyst_type, "regime": r.regime,
            "stop": r.suggested_stop, "risk_pct": r.suggested_risk_pct,
        }
        for r in ranked[:5]
    ]

    # 6. autonomous paper execution (gated)
    actions: list[dict] = []
    if place_orders:
        actions = _maybe_place_orders(ranked, nav, warnings, a_pos)

    report = MondayReport(
        timestamp=ts, regime=rg.regime, nav_usd=nav,
        schwab_position_count=len(s_tickers),
        alpaca_position_count=len(a_pos),
        alpaca_open_orders=len(a_orders),
        candidate_count=len(records),
        top=top, actions_taken=actions, warnings=warnings,
    )

    # log report
    write_cache("monday_open", "_report", "report", asdict(report))
    return report


def _maybe_place_orders(
    ranked, nav: Optional[float], warnings: list[str], existing_positions
) -> list[dict]:
    actions: list[dict] = []
    if not nav or nav <= 0:
        warnings.append("no NAV; refusing to place orders")
        return actions

    held = {p.get("symbol", "").upper() for p in existing_positions if p.get("symbol")}
    placed = 0
    for r in ranked:
        if placed >= MAX_NEW_PAPER_NAMES_PER_DAY:
            break
        if r.recommendation_class != RecommendationClass.BUY_READY.value:
            continue
        if r.ticker in held:
            continue
        if (r.dollar_volume_m or 0) < MIN_DOLLAR_VOLUME_M:
            continue
        if not r.suggested_risk_pct or not r.suggested_stop or not r.last_close:
            continue
        if r.suggested_risk_pct > MAX_RISK_PER_NAME_PCT:
            warnings.append(f"{r.ticker}: risk_pct cap")
            continue
        risk_dollars = nav * (r.suggested_risk_pct / 100)
        per_share_risk = max(r.last_close - r.suggested_stop, 0.01)
        qty = int(max(risk_dollars / per_share_risk, 0))
        if qty <= 0:
            continue
        try:
            order = alpaca.submit_order(
                r.ticker, qty=qty, side="buy", type_="market",
                time_in_force="day",
                client_order_id=f"druck-{r.date}-{r.ticker}",
            )
            actions.append({
                "ticker": r.ticker, "qty": qty, "stop": r.suggested_stop,
                "order_id": order.get("id"),
            })
            placed += 1
        except Exception as e:
            warnings.append(f"order {r.ticker} failed: {e}")
    return actions


def report_to_text(rep: MondayReport) -> str:
    lines = [
        f"DRUCK MONDAY-OPEN — {rep.timestamp}",
        f"Regime: {rep.regime}  NAV: {rep.nav_usd}",
        f"Schwab pos: {rep.schwab_position_count}  Alpaca pos: {rep.alpaca_position_count}  open orders: {rep.alpaca_open_orders}",
        f"Candidates scored: {rep.candidate_count}",
        "",
        "TOP 5:",
    ]
    for t in rep.top:
        lines.append(f"  {t['ticker']:6} {t['class']:15} score={t['score']:5.1f} setup={t['setup']:24} cat={t['catalyst']}")
    if rep.actions_taken:
        lines.append("")
        lines.append("PAPER ORDERS PLACED:")
        for a in rep.actions_taken:
            lines.append(f"  {a['ticker']:6} qty={a['qty']:4} stop={a['stop']} order_id={a['order_id']}")
    if rep.warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for w in rep.warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)
