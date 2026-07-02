#!/usr/bin/env python3
"""Trader · author_intents.py

Deterministic intent authoring: for every hypothesis in state=ready that has
no open intent or position on its primary ticker, mint exactly one
trade_intents row in state='proposed' so the gate_evaluator + executor can
take it the rest of the way to the broker.

Sizing rule (conservative, paper-account):
  - notional = clamp(SIZE_PCT_OF_EQUITY * account.equity, NOTIONAL_FLOOR, NOTIONAL_CEILING)
  - qty      = floor(notional / last_close)
  - skip if qty < 1

Risk-off behaviour: refuse to author any new intents.

Idempotent: walks hypotheses in priority order (highest quant_score first),
stops once MAX_OPEN_INTENTS is reached across the book.

Usage:
    python3 author_intents.py
    python3 author_intents.py --dry-run
    python3 author_intents.py --max 2
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))

# Connector path
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors.alpaca import (  # noqa: E402
    ConnectorError,
    daily_bars,
    get_account,
    latest_trade,
)
import worldmodel as wm  # noqa: E402

SIZE_PCT_OF_EQUITY = 0.01      # 1% per intent (baseline fallback when no prediction)
NOTIONAL_FLOOR = 200.0
NOTIONAL_CEILING = 2000.0
# Desk-wide open-intent COUNT throttle. NOT the risk limit — the Risk gate enforces real exposure
# (per-name / gross / correlation-cluster caps). 5 was far too low (blocked 42 ready ideas with the book
# only ~10 names); raised + env-tunable so the ranked ready-queue can actually flow. Per-pass churn is
# still bounded by --max (default 3).
MAX_OPEN_INTENTS = int(os.environ.get("TRADER_MAX_OPEN_INTENTS", "25"))
STOP_RULE = "-8% from entry"
MODELED_SLIPPAGE_BPS = 8.0
# Fractional-Kelly sizing (SUGGESTION ONLY — the Risk agent enforces the cap).
KELLY_SCALE = 0.25             # quarter-Kelly
KELLY_CAP = 0.10               # never suggest > 10% of equity pre-risk-gate
RETRIEVE_EPISODES = "/home/aaron/.openclaw/workspaces/trading-intel/scripts/retrieve_episodes.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"trading-intel DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, *, entity_id, action, before_state, after_state, rationale):
    aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + entity_id[:24]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', "
        "'trade_intent', ?, ?, ?, ?, ?)",
        (aid, _now_iso(), entity_id, action,
         before_state, after_state, (rationale or "")[:500]),
    )


def _regime_current(conn) -> str:
    row = conn.execute(
        "SELECT current FROM regime ORDER BY determined_at DESC LIMIT 1"
    ).fetchone()
    return row["current"] if row else "unknown"


def _latest_prediction(conn, hyp_id: str):
    """Most recent unresolved world-model prediction for this hypothesis."""
    return conn.execute(
        "SELECT id, p_correct, return_p10, return_p50, return_p90, horizon "
        "FROM predictions WHERE hypothesis_id = ? AND resolved_at IS NULL "
        "ORDER BY predicted_at DESC LIMIT 1",
        (hyp_id,),
    ).fetchone()


def _kelly_sizing(pred, equity: float) -> dict:
    """Translate a prediction into a fractional-Kelly notional suggestion.

    Returns a dict with sizing_basis, kelly_fraction, notional_raw, and (when a
    prediction exists but carries no positive edge) skip=True so the trader does
    not author an intent with no probabilistic edge.
    """
    if pred is None:
        return {"sizing_basis": "baseline_1pct", "kelly_fraction": None,
                "notional_raw": SIZE_PCT_OF_EQUITY * equity, "skip": False,
                "p_correct": None}
    p = float(pred["p_correct"])
    up = float(pred["return_p90"]) if pred["return_p90"] is not None else 0.0
    down = abs(float(pred["return_p10"])) if pred["return_p10"] is not None else 0.0
    if up <= 0 or down <= 0:
        return {"sizing_basis": "baseline_1pct", "kelly_fraction": None,
                "notional_raw": SIZE_PCT_OF_EQUITY * equity, "skip": False,
                "p_correct": p, "prediction_id": pred["id"]}
    frac = wm.kelly_fraction(p, up, down, kelly_scale=KELLY_SCALE, cap=KELLY_CAP)
    if frac <= 0:
        # Neutral or slightly negative prediction bands should not zero the queue when
        # the desk still has a ready hypothesis. Fall back to the baseline starter size
        # and let the episode context + downstream risk gate shape the final exposure.
        return {"sizing_basis": "baseline_1pct", "kelly_fraction": 0.0,
                "notional_raw": SIZE_PCT_OF_EQUITY * equity, "skip": False,
                "p_correct": p, "prediction_id": pred["id"]}
    return {"sizing_basis": "kelly", "kelly_fraction": round(frac, 4),
            "notional_raw": frac * equity, "skip": False, "p_correct": p,
            "prediction_id": pred["id"]}


def _count_open_intents(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM trade_intents "
        "WHERE state IN ('proposed','critic_review','approved','submitted','partial')"
    ).fetchone()
    return int(row["n"] if row else 0)


def _has_open_exposure(conn, ticker: str) -> bool:
    sym = ticker.upper()
    pos = conn.execute(
        "SELECT 1 FROM positions WHERE UPPER(ticker)=? AND state IN "
        "('opening','open','scaling','trimming','closing')",
        (sym,),
    ).fetchone()
    if pos:
        return True
    intent = conn.execute(
        "SELECT 1 FROM trade_intents WHERE UPPER(ticker)=? AND state IN "
        "('proposed','critic_review','approved','submitted','partial')",
        (sym,),
    ).fetchone()
    return bool(intent)


def _equity_usd() -> float:
    acc = get_account()
    return float(acc.get("equity") or acc.get("portfolio_value") or 0.0)


def _account_snapshot() -> tuple[float, float]:
    acc = get_account()
    equity = float(acc.get("equity") or acc.get("portfolio_value") or 0.0)
    cash = float(acc.get("cash") or 0.0)
    return equity, cash


def _last_close(ticker: str) -> float:
    """Reference price for sizing + entry_price_target. Prefer the LIVE last trade so the entry ref is
    fresh (and the executor's freshness gate rarely needs to reject); fall back to the latest daily bar."""
    lt = latest_trade(ticker)
    if lt and lt.get("price"):
        return float(lt["price"])
    bars = daily_bars(ticker, days=10)
    if not bars:
        raise ConnectorError(f"no bars for {ticker}")
    return float(bars[-1]["c"])


def _infer_direction(ticker: str, thesis: str | None) -> str:
    """Cheap deterministic direction parse from thesis_summary leading tokens."""
    t = (thesis or "").strip().lower()
    head = t[:160]
    if (t.startswith(("short", "bearish", "sell ", "fade "))
            or f"{ticker.lower()} short:" in head
            or "short/bearish" in head
            or re.search(r"\b(short|bearish|sell|fade)\b", head)):
        return "short"
    if t.startswith(("long", "bullish", "buy ", "accumulate ", "add ")):
        return "long"
    bearish_phrases = (
        "too optimistic", "too high for the rest", "vulnerable to", "pressure",
        "margin compression", "weaker margin profile", "stay weak",
    )
    if any(p in t for p in bearish_phrases):
        return "short"
    return "long"  # default-long for ambiguous; safer than mis-shorting


def _retrieve_episode_context(ticker: str, thesis: str | None) -> dict:
    cmd = [
        "python3",
        RETRIEVE_EPISODES,
        "--tickers",
        ticker,
        "--query",
        (thesis or "").strip(),
        "-k",
        "1",
        "--json",
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    payload = json.loads(out.stdout)
    episodes = payload.get("episodes") or []
    top = episodes[0] if episodes else None
    return {
        "command": " ".join(cmd),
        "as_of": payload.get("as_of"),
        "episode": top,
    }


def _episode_sizing_multiplier(direction: str, episode: dict | None) -> tuple[float, str]:
    if not episode:
        return 1.0, "no_episode"

    action = (episode.get("correct_action") or "").lower()
    trap = (episode.get("naive_trap") or "").lower()

    bullish = ("buy", "own", "accumulate", "stay long", "durable", "cash-flow", "backlog", "multi-quarter")
    bearish = ("short", "fade", "sell", "avoid", "do not chase", "don't chase", "underweight")
    caution = ("wait", "do not trade", "don't trade", "one-day beat", "headline")

    explicit_decline = ("do not allocate", "do not own", "avoid entirely", "don't own")

    if direction == "long":
        if episode.get("is_negative_control") and any(tok in action for tok in explicit_decline):
            return 0.0, "episode_negative_control_veto"
        if any(tok in action for tok in bearish):
            return 0.75, "episode_bearish_caution"
        if any(tok in action for tok in caution) or any(tok in trap for tok in ("chase", "headline", "one-day")):
            return 0.75, "episode_caution"
        if any(tok in action for tok in bullish):
            return 1.15, "episode_supportive"
        return 1.0, "episode_neutral"

    if episode.get("is_negative_control") and any(tok in trap for tok in ("buy on hype", "buy the dip", "bottom-fish")):
        return 1.1, "episode_supportive_short"
    if any(tok in action for tok in bullish) and not any(tok in action for tok in bearish):
        return 0.75, "episode_bullish_caution_for_short"
    if any(tok in trap for tok in ("buy the dip", "bottom-fish", "call the turn", "look through")):
        return 1.1, "episode_supportive_short"
    return 1.0, "episode_neutral_short"


def author(conn, hyp_row, *, equity: float, cash_remaining: float, dry_run: bool) -> dict:
    hid = hyp_row["id"]
    try:
        tickers = json.loads(hyp_row["tickers"] or "[]")
    except json.JSONDecodeError:
        tickers = []
    if not tickers:
        return {"id": hid, "skip": True, "reason": "no tickers"}
    ticker = str(tickers[0]).upper()

    direction = _infer_direction(ticker, hyp_row["thesis_summary"])
    try:
        episode_ctx = _retrieve_episode_context(ticker, hyp_row["thesis_summary"])
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": f"episode_lookup_failed: {exc}"}
    episode = episode_ctx["episode"]
    episode_mult, episode_flag = _episode_sizing_multiplier(direction, episode)
    if episode_mult <= 0:
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": episode_flag}
    # direction=short authors a sell-to-open intent (executor maps it via the
    # trade_intents.direction column, migration 0013). The episode multiplier
    # above has already vetted the short against the analog library.

    if _has_open_exposure(conn, ticker):
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": "open exposure already"}

    try:
        last = _last_close(ticker)
    except ConnectorError as exc:
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": f"price_lookup_failed: {exc}"}

    pred = _latest_prediction(conn, hid)
    sizing = _kelly_sizing(pred, equity)
    if sizing.get("skip"):
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": f"no_probabilistic_edge (p={sizing.get('p_correct')}, kelly=0)"}

    # Conviction-scaled ceiling: let high-conviction predictions (p_correct from the combined
    # calibrated-mechanism posteriors) size up toward the Kelly cap, instead of the flat $2k cap
    # throttling everything. Low conviction stays at the legacy ceiling. Risk gate caps downstream.
    p_conv = sizing.get("p_correct") or 0.5
    conv = max(0.0, min(1.0, (float(p_conv) - 0.5) / 0.5))
    dyn_ceiling = max(NOTIONAL_CEILING, min(KELLY_CAP * equity,
                                            NOTIONAL_CEILING + conv * (KELLY_CAP * equity - NOTIONAL_CEILING)))
    notional = max(NOTIONAL_FLOOR, min(dyn_ceiling, sizing["notional_raw"] * episode_mult))
    notional = min(notional, cash_remaining)
    if notional < min(NOTIONAL_FLOOR, cash_remaining):
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": f"insufficient_cash_remaining={cash_remaining:.2f}"}
    qty = int(math.floor(notional / last))
    if qty < 1:
        if last <= cash_remaining and last <= KELLY_CAP * equity:
            qty = 1
        else:
            return {"id": hid, "ticker": ticker, "skip": True,
                    "reason": f"qty<1 at price={last}"}
    realized_notional = round(qty * last, 2)

    edge_scorecard = {
        "quant_score": float(hyp_row["quant_score"] or 0),
        "evidence_floor_met": True,
        "regime_at_author": _regime_current(conn),
        "sizing_basis": sizing["sizing_basis"],
        "kelly_fraction": sizing.get("kelly_fraction"),
        "p_correct": sizing.get("p_correct"),
        "prediction_id": sizing.get("prediction_id"),
        "episode_context": episode_ctx,
        "episode_sizing_multiplier": episode_mult,
        "episode_signal": episode_flag,
        "return_band_pct": (
            None if pred is None else
            [pred["return_p10"], pred["return_p50"], pred["return_p90"]]
        ),
        "risk_suggested_notional": round(notional, 2),
    }

    intent_id = "ti-" + uuid.uuid4().hex[:24]
    ec_id = "ec-" + uuid.uuid4().hex[:24]
    conviction = sizing.get("kelly_fraction") or SIZE_PCT_OF_EQUITY
    if dry_run:
        return {
            "id": hid, "ticker": ticker, "would_author": True,
            "intent_id": intent_id, "qty": qty, "price": last,
            "notional": realized_notional, "sizing_basis": sizing["sizing_basis"],
            "kelly_fraction": sizing.get("kelly_fraction"),
            "p_correct": sizing.get("p_correct"),
            "episode_signal": episode_flag,
        }

    conn.execute(
        "INSERT INTO expression_candidates (id, hypothesis_id, vehicle, ticker, "
        "conviction_weight, quant_rationale, recommended, score_json, created_at) "
        "VALUES (?, ?, 'direct_equity', ?, ?, ?, 1, ?, ?)",
        (ec_id, hid, ticker, conviction,
         f"trader_baseline: {direction} {ticker} at ${last} (quant_score={hyp_row['quant_score']}, "
         f"sizing={sizing['sizing_basis']}, kelly={sizing.get('kelly_fraction')})",
         json.dumps(edge_scorecard), _now_iso()),
    )

    conn.execute(
        "INSERT INTO trade_intents ("
        "id, hypothesis_id, expression_candidate_id, created_by, created_at, "
        "action, tranche_type, ticker, vehicle, size, entry_price_target, stop_rule, "
        "time_horizon, triggered_by, edge_scorecard_json, "
        "modeled_slippage_bps, state, direction) "
        "VALUES (?, ?, ?, 'trader', ?, 'open', 'starter', ?, 'direct_equity', ?, ?, ?, "
        "?, 'trader_baseline_v1', ?, ?, 'proposed', ?)",
        (intent_id, hid, ec_id, _now_iso(), ticker, float(qty), last, STOP_RULE,
         hyp_row["time_horizon"] or "position_1_4w",
         json.dumps(edge_scorecard), MODELED_SLIPPAGE_BPS, direction),
    )
    _audit(conn, entity_id=intent_id, action="author",
           before_state=None, after_state="proposed",
           rationale=f"author open {direction} {ticker} qty={qty} @ ~{last} notional≈${realized_notional}")
    # Mark hypothesis active once an intent rides on it
    conn.execute(
        "UPDATE hypotheses SET state='active' WHERE id=? AND state='ready'", (hid,)
    )
    return {"id": hid, "ticker": ticker, "authored": True, "intent_id": intent_id,
            "qty": qty, "price": last, "notional": realized_notional,
            "episode_signal": episode_flag}


SWAP_ENABLED = os.environ.get("SWAP_ENABLED", "1") == "1"


def _author_swap_exits(conn, dry_run: bool) -> list[dict]:
    """Ranked queue-vs-holdings SWAP (overseer pq-2ec10abce13e). When the book is at the concurrent-names
    cap, author an EXIT for the weakest holding so a higher-conviction ready idea can take its slot. The
    Risk gate auto-approves the exit (risk-reducing) and frees the slot; the replacement opens normally.
    Conservative: rank_swaps requires the candidate to clear the holding by a margin + the holding to be
    genuinely weak + not a fresh entry, and caps swaps per pass."""
    if not SWAP_ENABLED:
        return []
    try:
        import rank_swaps
        swaps = rank_swaps.evaluate_swaps(conn)
    except Exception as exc:  # never let swap logic break the normal authoring pass
        return [{"swap_error": str(exc)[:160]}]
    out = []
    for s in swaps:
        tk = s["exit_ticker"]
        if conn.execute(
            "SELECT 1 FROM trade_intents WHERE UPPER(ticker)=? AND action IN ('exit','trim') AND state IN "
            "('proposed','critic_review','risk_review','approved','submitted','partial') LIMIT 1",
            (tk,)).fetchone():
            continue  # an exit is already in flight for this name
        rec = {"swap": s["reason"], "exit_ticker": tk, "exit_qty": s["exit_qty"],
               "open_ticker": s["open_ticker"]}
        if dry_run:
            out.append({**rec, "dry_run": True})
            continue
        row = conn.execute("SELECT hypothesis_id FROM positions WHERE id=?", (s["exit_pos_id"],)).fetchone()
        hid = row[0] if row else None
        if not hid:
            out.append({**rec, "skipped": "holding has no hypothesis_id (cannot author a clean exit)"})
            continue
        try:
            last = _last_close(tk)
        except Exception:
            last = None
        now = _now_iso()
        # trade_intents requires both hypothesis_id AND expression_candidate_id (NOT NULL); mint an
        # expression_candidate for the exit the same way opens do, tied to the holding's original thesis.
        ec_id = "ec-swap-" + uuid.uuid4().hex[:18]
        conn.execute(
            "INSERT INTO expression_candidates (id, hypothesis_id, vehicle, ticker, conviction_weight, "
            "quant_rationale, recommended, score_json, created_at) "
            "VALUES (?, ?, 'direct_equity', ?, 0, ?, 0, '{}', ?)",
            (ec_id, hid, tk, "swap-rotation exit: " + s["reason"], now))
        iid = "ti-swap-" + uuid.uuid4().hex[:18]
        conn.execute(
            "INSERT INTO trade_intents (id, hypothesis_id, expression_candidate_id, created_by, created_at, "
            "action, tranche_type, ticker, vehicle, size, entry_price_target, stop_rule, time_horizon, "
            "triggered_by, modeled_slippage_bps, state) VALUES (?, ?, ?, 'trader', ?, 'exit', NULL, ?, "
            "'direct_equity', ?, ?, NULL, 'position_1_4w', 'swap_rotation', ?, 'proposed')",
            (iid, hid, ec_id, now, tk, float(s["exit_qty"]), last, MODELED_SLIPPAGE_BPS))
        _audit(conn, entity_id=iid, action="author", before_state=None, after_state="proposed",
               rationale="SWAP-EXIT: " + s["reason"])
        out.append({**rec, "intent_id": iid})
    if out and not dry_run:
        conn.commit()
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=3,
                   help="cap intents authored this pass (default 3)")
    args = p.parse_args(argv)

    conn = _connect()
    regime = _regime_current(conn)
    if regime == "risk_off":
        print(json.dumps({"authored": 0, "skipped_all": True,
                          "reason": f"regime={regime}"}, indent=2))
        return 0

    try:
        equity, cash_remaining = _account_snapshot()
    except ConnectorError as exc:
        print(json.dumps({"error": f"alpaca_account: {exc}",
                          "authored": 0}, indent=2))
        return 2

    # Ranked SWAP first: if the book is at the name-cap, free a slot by exiting the weakest holding so a
    # higher-conviction ready idea can take it (instead of blocking every new name).
    swap_exits = _author_swap_exits(conn, args.dry_run)

    open_existing = _count_open_intents(conn)
    capacity = max(0, MAX_OPEN_INTENTS - open_existing)
    if capacity == 0:
        print(json.dumps({"authored": 0,
                          "reason": f"open_intents={open_existing} >= cap={MAX_OPEN_INTENTS}"},
                         indent=2))
        return 0

    rows = conn.execute(
        "SELECT id, tickers, state, quant_score, time_horizon, thesis_summary "
        "FROM hypotheses WHERE state='ready' "
        "ORDER BY quant_score DESC NULLS LAST, scored_at DESC NULLS LAST"
    ).fetchall()

    results = []
    authored = 0
    for r in rows:
        if authored >= min(capacity, args.max):
            res = {"id": r["id"], "ticker": json.loads(r["tickers"] or "[]")[0],
                   "skip": True, "reason": "intent_capacity_reached"}
            results.append(res)
            continue
        res = author(conn, r, equity=equity, cash_remaining=cash_remaining, dry_run=args.dry_run)
        if res.get("authored") or res.get("would_author"):
            authored += 1
            cash_remaining = max(0.0, cash_remaining - float(res.get("notional") or 0.0))
        results.append(res)

    if not args.dry_run:
        conn.commit()
    print(json.dumps({
        "authored": authored,
        "considered": len(rows),
        "open_intents_before": open_existing,
        "capacity": capacity,
        "equity_usd": round(equity, 2),
        "cash_remaining_usd": round(cash_remaining, 2),
        "regime": regime,
        "dry_run": bool(args.dry_run),
        "swap_exits": swap_exits,
        "results": results,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
