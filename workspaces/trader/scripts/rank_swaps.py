#!/usr/bin/env python3
"""rank_swaps.py — ranked queue-vs-holdings swap engine (TM-206, overseer pq-b461074b6dc0).

`author_intents._author_swap_exits` has imported this module since the swap feature landed —
but the module itself was never committed, so every pass silently returned
`{"swap_error": "No module named 'rank_swaps'"}` and the at-cap replacement path NO-OPED
(found during the 2026-07-24 board clear). This implements the documented contract:

When the book is at the concurrent-names cap, propose exiting the WEAKEST holdings so the
STRONGEST ready ideas can take their slots. Conservative by design:
  * only fires when open names >= the cap (otherwise there is nothing to free),
  * the candidate must clear the holding's score by SWAP_MARGIN (no lateral churn),
  * the holding must be genuinely weak (stale/low quant_score, non-positive P&L),
  * fresh entries are protected (MIN_HOLDING_AGE_D) — no whipsawing new positions,
  * at most MAX_SWAPS_PER_PASS proposals per pass.

Deterministic ranking only; the proposed exits still flow through the normal intent path
(risk gate auto-approves risk-reducing exits) and the replacement opens go through the full
score → critic → risk stack. This module never writes to the store.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

MAX_POSITIONS_ASSUMED = int(os.environ.get("TRADER_MAX_POSITIONS_ASSUMED", "48"))
SWAP_MARGIN = float(os.environ.get("SWAP_MARGIN", "15"))          # candidate must beat holding by this many quant points
WEAK_SCORE_MAX = float(os.environ.get("SWAP_WEAK_SCORE_MAX", "60"))
MIN_HOLDING_AGE_D = float(os.environ.get("SWAP_MIN_HOLDING_AGE_D", "5"))
MAX_SWAPS_PER_PASS = int(os.environ.get("SWAP_MAX_PER_PASS", "2"))
OPEN_STATES = ("opening", "open", "scaling", "trimming", "closing")


def _age_days(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def evaluate_swaps(conn) -> list[dict]:
    ph = ",".join("?" * len(OPEN_STATES))
    holdings = conn.execute(
        f"SELECT p.id pos_id, UPPER(p.ticker) ticker, p.qty, p.opened_at, p.unrealized_pnl_pct, "
        f"       (SELECT h.quant_score FROM hypotheses h WHERE h.id = p.hypothesis_id) hyp_score, "
        f"       (SELECT MAX(h2.quant_score) FROM hypotheses h2 WHERE h2.tickers LIKE '%\"' || UPPER(p.ticker) || '\"%' "
        f"        AND h2.state IN ('active','ready','scored')) latest_score "
        f"FROM positions p WHERE p.state IN ({ph}) AND p.qty != 0",
        OPEN_STATES,
    ).fetchall()
    n_names = len({h["ticker"] for h in holdings})
    if n_names < MAX_POSITIONS_ASSUMED:
        return []  # not at the cap — normal authoring has headroom, no swaps needed

    held = {h["ticker"] for h in holdings}
    candidates = [
        dict(r) for r in conn.execute(
            "SELECT id, tickers, quant_score FROM hypotheses WHERE state='ready' "
            "AND quant_score IS NOT NULL ORDER BY quant_score DESC LIMIT 12"
        )
    ]
    cands = []
    for c in candidates:
        try:
            tk = str(json.loads(c["tickers"] or "[]")[0]).upper()
        except (ValueError, IndexError):
            continue
        if tk not in held:
            cands.append({"ticker": tk, "score": float(c["quant_score"])})

    weak = []
    for h in holdings:
        score = h["latest_score"] if h["latest_score"] is not None else h["hyp_score"]
        age = _age_days(h["opened_at"])
        pnl = h["unrealized_pnl_pct"]
        if age < MIN_HOLDING_AGE_D:
            continue  # protect fresh entries from whipsaw
        if score is not None and float(score) >= WEAK_SCORE_MAX:
            continue  # holding still carries a strong current score
        if pnl is not None and float(pnl) > 0:
            continue  # winners are exited by stops/horizons, not swaps
        weak.append({"pos_id": h["pos_id"], "ticker": h["ticker"], "qty": h["qty"],
                     "score": float(score) if score is not None else None,
                     "pnl": float(pnl) if pnl is not None else None, "age_d": round(age, 1)})
    # weakest first: no-score before low-score, then most-negative P&L
    weak.sort(key=lambda w: (w["score"] if w["score"] is not None else -1.0,
                             w["pnl"] if w["pnl"] is not None else 0.0))

    out = []
    for w in weak:
        if not cands or len(out) >= MAX_SWAPS_PER_PASS:
            break
        c = cands[0]
        floor = (w["score"] if w["score"] is not None else 50.0) + SWAP_MARGIN
        if c["score"] < floor:
            break  # strongest remaining candidate cannot clear even the weakest holding
        cands.pop(0)
        out.append({
            "exit_pos_id": w["pos_id"],
            "exit_ticker": w["ticker"],
            "exit_qty": abs(float(w["qty"])),
            "open_ticker": c["ticker"],
            "reason": (f"at name-cap ({n_names}/{MAX_POSITIONS_ASSUMED}); {w['ticker']} weak "
                       f"(score={w['score']}, pnl={w['pnl']}%, age={w['age_d']}d) -> "
                       f"{c['ticker']} scores {c['score']:.0f} (margin {SWAP_MARGIN:.0f} cleared)"),
        })
    return out


if __name__ == "__main__":
    import sqlite3
    conn = sqlite3.connect("file:" + str(Path(os.path.expanduser(
        "~/.openclaw/state/trading-intel.sqlite"))) + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    print(json.dumps(evaluate_swaps(conn), indent=1))
