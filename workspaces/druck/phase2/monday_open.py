"""Checkpoint/open orchestrator (AUTONOMOUS_PM_OPERATING_MODEL.md §7 + MONDAY_OPEN_RUNBOOK.md).

Single command end-to-end:
  1. Refresh Alpaca paper state
  2. Compute regime
  3. Generate candidates
  4. Normalize + score each (with Alpaca live confirmation on)
  5. Surface top actions
  6. (Optional) place Alpaca paper orders for buy_ready names within policy
  7. Log results
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Optional

from . import ats_v6, candidate_gen, normalize as norm, regime as regime_mod
from .adapters import alpaca, sheets
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
    alpaca_position_count: int
    alpaca_open_orders: int
    candidate_count: int
    top: list[dict]
    actions_taken: list[dict]
    warnings: list[str]


def _risk_config() -> dict[str, Any]:
    return (ats_v6.load_runtime_config().get("risk") or {})


def _runtime_mode_cfg(risk_cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    runtime_cfg = risk_cfg.get("runtime") or {}
    mode_name = runtime_cfg.get("startup_mode") or "seed"
    return mode_name, (risk_cfg.get("modes") or {}).get(mode_name, {})


def _effective_max_position_pct(risk_cfg: dict[str, Any]) -> float:
    global_limits = risk_cfg.get("global_limits") or {}
    mode_name, mode_cfg = _runtime_mode_cfg(risk_cfg)
    max_pos_cfg = global_limits.get("max_position_pct") or {}
    base = float(max_pos_cfg.get("opportunistic") or max_pos_cfg.get("conviction") or 0)
    override = mode_cfg.get("max_position_pct_override")
    if override is not None:
        try:
            base = min(base, float(override))
        except (TypeError, ValueError):
            pass
    if base <= 0:
        raise RuntimeError(f"invalid max position pct for mode {mode_name}")
    return base


def _effective_max_new_entries_per_day(risk_cfg: dict[str, Any]) -> int:
    global_limits = risk_cfg.get("global_limits") or {}
    configured = int(global_limits.get("max_new_entries_per_day") or MAX_NEW_PAPER_NAMES_PER_DAY)
    return max(1, min(configured, MAX_NEW_PAPER_NAMES_PER_DAY))


def _effective_min_dollar_volume_m(risk_cfg: dict[str, Any]) -> float:
    eligibility = risk_cfg.get("eligibility") or {}
    default_usd = ((eligibility.get("min_dollar_volume_usd") or {}).get("default")) or 0
    configured_m = float(default_usd) / 1_000_000 if default_usd else 0.0
    return max(MIN_DOLLAR_VOLUME_M, configured_m)


def _broker_health_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if w.startswith("alpaca.") or w.startswith("normalize(")]


def _execution_blockers(
    *,
    risk_cfg: dict[str, Any],
    warnings: list[str],
    open_orders: list[dict],
) -> list[str]:
    hard_gates = risk_cfg.get("hard_gates") or {}
    blockers: list[str] = []
    if open_orders and hard_gates.get("block_on_unresolved_order_state", False):
        blockers.append(f"unresolved open orders present: {len(open_orders)}")
    if _broker_health_warnings(warnings) and hard_gates.get("block_on_broker_api_instability", False):
        blockers.append("broker/data instability warnings present")
    return blockers


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
    risk_cfg = _risk_config()

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
        actions = _maybe_place_orders(
            ranked,
            nav,
            warnings,
            a_pos,
            a_orders,
            risk_cfg,
        )

    report = MondayReport(
        timestamp=ts, regime=rg.regime, nav_usd=nav,
        alpaca_position_count=len(a_pos),
        alpaca_open_orders=len(a_orders),
        candidate_count=len(records),
        top=top, actions_taken=actions, warnings=warnings,
    )

    # log report
    write_cache("monday_open", "_report", "report", asdict(report))
    return report


def _maybe_place_orders(
    ranked,
    nav: Optional[float],
    warnings: list[str],
    existing_positions,
    open_orders: list[dict],
    risk_cfg: dict[str, Any],
) -> list[dict]:
    actions: list[dict] = []
    if not nav or nav <= 0:
        warnings.append("no NAV; refusing to place orders")
        return actions

    blockers = _execution_blockers(
        risk_cfg=risk_cfg,
        warnings=warnings,
        open_orders=open_orders,
    )
    if blockers:
        warnings.extend([f"execution blocked: {b}" for b in blockers])
        return actions

    held = {p.get("symbol", "").upper() for p in existing_positions if p.get("symbol")}
    max_new_entries = _effective_max_new_entries_per_day(risk_cfg)
    max_position_pct = _effective_max_position_pct(risk_cfg)
    min_dollar_volume_m = _effective_min_dollar_volume_m(risk_cfg)
    execution_cfg = risk_cfg.get("execution") or {}
    market_entry_allow = set(execution_cfg.get("allow_market_entries_only_for") or [])
    placed = 0
    for r in ranked:
        if placed >= max_new_entries:
            break
        if r.recommendation_class != RecommendationClass.BUY_READY.value:
            continue
        if r.ticker in held:
            continue
        if (r.dollar_volume_m or 0) < min_dollar_volume_m:
            continue
        if not r.suggested_risk_pct or not r.suggested_stop or not r.last_close:
            continue
        if r.suggested_risk_pct > MAX_RISK_PER_NAME_PCT:
            warnings.append(f"{r.ticker}: risk_pct cap")
            continue
        entry_price = float(r.last_close)
        entry_style = "limit"
        limit_price = entry_price
        if r.alpaca_last:
            entry_price = float(r.alpaca_last)
        if entry_price <= 0:
            warnings.append(f"{r.ticker}: invalid entry price")
            continue
        risk_dollars = nav * (r.suggested_risk_pct / 100)
        per_share_risk = max(entry_price - r.suggested_stop, 0.01)
        qty = int(max(risk_dollars / per_share_risk, 0))
        if qty <= 0:
            continue
        max_notional = nav * (max_position_pct / 100.0)
        qty = min(qty, int(max_notional / entry_price))
        if qty <= 0:
            warnings.append(f"{r.ticker}: position cap ({max_position_pct:.2f}% NAV) blocks order")
            continue
        if "stop_exit" not in market_entry_allow and "high_liquidity_exit" not in market_entry_allow:
            entry_style = "limit"
            limit_price = round(entry_price, 2)
        try:
            order = alpaca.submit_order(
                r.ticker, qty=qty, side="buy", type_=entry_style,
                time_in_force="day",
                limit_price=limit_price if entry_style == "limit" else None,
                client_order_id=f"druck-{r.date}-{r.ticker}",
            )
            actions.append({
                "ticker": r.ticker, "qty": qty, "stop": r.suggested_stop,
                "entry_style": entry_style,
                "limit_price": limit_price if entry_style == "limit" else None,
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
        f"Alpaca pos: {rep.alpaca_position_count}  open orders: {rep.alpaca_open_orders}",
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
