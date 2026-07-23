#!/usr/bin/env python3
"""decompose_events.py — same-day second-order decomposition of big single-name moves.

The measured research failure this attacks: `research:big_story_direction` = 0/3 — on the
month's biggest single-name stories the desk was WRONG-SIDE on every one, because at event
time nothing does the second-order reasoning (the Kimi/NVDA class of miss: consensus said
"competition shock, bad for semis"; the primary-source second-order read — the competitor
still buys GB300s ⇒ demand intact ⇒ overreaction — was the money). resolve_challenged
brings that reasoning to STALE theses; this brings it to FRESH events, the front of the
funnel, the day the story breaks.

Deterministic harness, LLM judgement only: pick the biggest recent single-name moves from
`market_events` (the debrief's own recorded moves), give a STRONG model the story + price
context + our current exposure, and force the decomposition: consensus vs components vs
what's priced vs the named falsifier. An actionable stance (long/short) is written as a
hypothesis in state='scored' — it flows through quant scoring → the critic's 10 gates →
the non-bypassable Risk gate like any other idea and cannot reach the broker un-vetted.
no_trade verdicts are printed (and count as deliberate passes), not written.

Measured by the integrity scoreboard: research:big_story_direction should climb off 0/3.

  python3 decompose_events.py --dry-run            # print decompositions, write nothing
  python3 decompose_events.py --max 3               # decompose up to 3 movers (writes)
  DECOMP_MODEL=claude-opus-4-8 python3 decompose_events.py ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors.marketdata import daily_bars  # noqa: E402  (Massive-backed)

DB_PATH = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
MODEL = os.environ.get("DECOMP_MODEL", "claude-sonnet-5")
BIG_MOVE_PCT = 2.0
LOOKBACK_DAYS = 3
_INDEX = {"SPY", "QQQ", "SOXX", "IWM", "DIA", "VIX", "GLD", "TLT"}
OPEN_STATES = ("opening", "open", "scaling", "trimming", "closing")

PROMPT = """You are a sharp, contrarian buy-side analyst decomposing a big single-name move THE DAY
IT HAPPENS — before the desk decides anything. The desk's measured failure mode is parroting the
consensus narrative and ending up wrong-side of every big story. Your job is the second-order read.

The standard: when Kimi K3 "competition shock" hit AI semis, consensus said "bad for NVDA" and the
desk stayed wrong; the second-order read — a primary source showed the competitor itself ACQUIRED
Nvidia GB300 servers to train ⇒ picks-and-shovels demand intact ⇒ selloff overdone — was the money.
Find that layer here, or honestly conclude there is no edge and say no_trade.

THE MOVE
  ticker: {ticker}   move: {move:+.1f}% on {date}
  recorded market narrative(s): {events}
  price context: {price_ctx}
  our current exposure/thesis: {exposure}

Decompose:
1. FIRST-ORDER: the consensus story for this move, in one sentence.
2. SECOND-ORDER: break the real driver into components (company-specific vs sector/macro flow;
   demand vs margin vs multiple; genuine new information vs repricing of known facts; who up/down
   the value chain is mispriced by this narrative).
3. PRICED-IN: after this move, what does the price already assume? Is the reaction overdone,
   justified, or insufficient?
4. STANCE: long / short / no_trade — with the SPECIFIC checkable fact that would falsify you.
   no_trade is a respectable answer; a forced opinion with no edge is how the desk lost.

Output STRICT JSON, nothing else:
{{"stance":"long|short|no_trade",
  "conviction":0.0-1.0,
  "first_order":"one sentence",
  "second_order":"the real decomposition, 2-4 sentences",
  "priced_in":"what the price now assumes; overdone/justified/insufficient",
  "falsifier":"the specific checkable fact that would flip this call",
  "key_facts":["specific facts driving the call"],
  "thesis":"one-paragraph falsifiable thesis a PM could act on (empty string if no_trade)"}}"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conf_enum(c) -> str:
    try:
        c = float(c)
    except (TypeError, ValueError):
        return "medium"
    return "high" if c >= 0.66 else ("low" if c < 0.4 else "medium")


def _tracked_universe(conn) -> set[str]:
    """Names the desk owns or has recently thought about — the book + live idea flow."""
    names: set[str] = set()
    for r in conn.execute(
        f"SELECT DISTINCT UPPER(ticker) t FROM positions WHERE state IN ({','.join('?'*len(OPEN_STATES))})",
        OPEN_STATES,
    ):
        if r["t"]:
            names.add(r["t"])
    for r in conn.execute(
        "SELECT tickers FROM hypotheses WHERE created_at >= datetime('now','-30 days')"
    ):
        try:
            names.update(str(t).upper() for t in json.loads(r["tickers"] or "[]"))
        except (ValueError, TypeError):
            continue
    return names - _INDEX


def find_candidates(conn, max_n: int, only_ticker: str | None = None) -> list[dict]:
    """Biggest recent single-name moves. Two sources: (a) the debrief's recorded moves
    when present, (b) 1-day returns COMPUTED from Massive over the tracked universe —
    because the debrief's observed_moves_json silently went empty after 2026-07-17
    (found 2026-07-23), and a candidate source must not depend on another LLM having
    remembered to record numbers."""
    best: dict[str, dict] = {}

    # (a) recorded moves, when the debrief provided them
    for r in conn.execute(
        "SELECT event_date, observed_moves_json FROM market_events "
        "WHERE event_date >= date('now', ?) AND observed_moves_json IS NOT NULL",
        (f"-{LOOKBACK_DAYS} days",),
    ):
        try:
            moves = json.loads(r["observed_moves_json"])
        except (ValueError, TypeError):
            continue
        for tk, mv in (moves or {}).items():
            if tk in _INDEX or not isinstance(mv, (int, float)) or abs(mv) < BIG_MOVE_PCT:
                continue
            if tk not in best or abs(mv) > abs(best[tk]["move"]):
                best[tk] = {"ticker": tk, "move": float(mv), "date": r["event_date"], "src": "debrief"}

    # (b) computed 1-day moves over the tracked universe (deterministic, debrief-independent)
    universe = {only_ticker.upper()} if only_ticker else _tracked_universe(conn)
    for tk in sorted(universe):
        try:
            bars = daily_bars(tk, days=6)
            c = [b for b in bars if b.get("c")]
            if len(c) < 2:
                continue
            mv = (float(c[-1]["c"]) / float(c[-2]["c"]) - 1.0) * 100.0
        except Exception:
            continue
        if abs(mv) >= BIG_MOVE_PCT and (tk not in best or abs(mv) > abs(best[tk]["move"])):
            best[tk] = {"ticker": tk, "move": round(mv, 2), "date": c[-1]["t"], "src": "computed"}

    if only_ticker:
        best = {k: v for k, v in best.items() if k == only_ticker.upper()}

    out = []
    for tk, c in sorted(best.items(), key=lambda kv: -abs(kv[1]["move"])):
        fresh = conn.execute(
            "SELECT 1 FROM hypothesis_evidence e JOIN hypotheses h ON h.id=e.hypothesis_id "
            "WHERE h.tickers LIKE ? AND e.indicator='second_order' "
            "AND e.retrieved_at >= datetime('now','-3 days') LIMIT 1",
            (f'%"{tk}"%',),
        ).fetchone()
        if fresh:
            continue  # already deep-decomposed recently (resolver or a prior run)
        out.append(c)
        if len(out) >= max_n:
            break
    return out


def _price_ctx(ticker: str) -> str:
    try:
        bars = daily_bars(ticker, days=70)
        c = [b["c"] for b in bars if b.get("c")]
        if len(c) < 22:
            return "insufficient history"
        return (f"now ${c[-1]:.2f}; 5d {((c[-1]/c[-6])-1)*100:+.1f}%, "
                f"21d {((c[-1]/c[-22])-1)*100:+.1f}%, 63d {((c[-1]/c[0])-1)*100:+.1f}%")
    except Exception:
        return "unavailable"


def _exposure(conn, ticker: str) -> str:
    pos = conn.execute(
        f"SELECT qty, COALESCE(current_value, qty*cost_basis) v FROM positions "
        f"WHERE UPPER(ticker)=? AND state IN ({','.join('?'*len(OPEN_STATES))})",
        (ticker, *OPEN_STATES),
    ).fetchone()
    hyp = conn.execute(
        "SELECT state, substr(thesis_summary,1,150) t FROM hypotheses WHERE tickers LIKE ? "
        "ORDER BY created_at DESC LIMIT 1", (f'%"{ticker}"%',),
    ).fetchone()
    bits = []
    if pos and pos["qty"]:
        bits.append(f"holding {pos['qty']:+.1f} shares (${(pos['v'] or 0):,.0f})")
    if hyp:
        bits.append(f"latest thesis [{hyp['state']}]: {hyp['t']}")
    return "; ".join(bits) or "no position, no thesis"


def _events_text(conn, ticker: str, date: str) -> str:
    rows = conn.execute(
        "SELECT event_date, headline FROM market_events WHERE event_date >= date(?, '-2 days') "
        "ORDER BY event_date DESC LIMIT 6", (date,),
    ).fetchall()
    return "\n".join(f"- {r['event_date']}: {(r['headline'] or '')[:200]}" for r in rows) or "(none recorded)"


def decompose(conn, cand: dict) -> dict | None:
    prompt = PROMPT.format(
        ticker=cand["ticker"], move=cand["move"], date=cand["date"],
        events=_events_text(conn, cand["ticker"], cand["date"]),
        price_ctx=_price_ctx(cand["ticker"]),
        exposure=_exposure(conn, cand["ticker"]),
    )
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", MODEL], input=prompt,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        print(f"  claude -p failed rc={r.returncode}: {r.stderr[:150]}", file=sys.stderr)
        return None
    m = re.search(r"\{.*\}", r.stdout, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def write_hypothesis(conn, cand: dict, res: dict) -> str | None:
    """Actionable stance -> gated hypothesis (state='scored'); judgement/prose only."""
    stance = (res.get("stance") or "no_trade").lower()
    if stance not in ("long", "short") or not (res.get("thesis") or "").strip():
        return None
    now = _now()
    hid = "hyp-evd-" + uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO hypotheses (id, created_at, created_by, tickers, thesis_summary, state, "
        "confidence, time_horizon, rationale_concise) VALUES (?,?,'researcher',?,?,'scored',?,"
        "'position_1_4w',?)",
        (hid, now, json.dumps([cand["ticker"]]),
         f"[{stance.upper()} {cand['ticker']}] event-decomposition: {res['thesis'][:260]}",
         _conf_enum(res.get("conviction")), (res.get("second_order") or "")[:500]),
    )
    for ind, val in (("second_order", res.get("second_order")),
                     ("priced_in", res.get("priced_in")),
                     ("falsifier", res.get("falsifier"))):
        if val:
            conn.execute(
                "INSERT INTO hypothesis_evidence (id, hypothesis_id, indicator, value, source, "
                "retrieved_at, as_of, signal_type) VALUES (?,?,?,?,'decompose_events',?,date('now'),'llm_decomposition')",
                ("ev-" + uuid.uuid4().hex[:12], hid, ind, str(val)[:500], now),
            )
    for f in (res.get("key_facts") or [])[:5]:
        conn.execute(
            "INSERT INTO hypothesis_evidence (id, hypothesis_id, indicator, value, source, "
            "retrieved_at, as_of, signal_type) VALUES (?,?,'key_fact',?,'decompose_events',?,date('now'),'llm_decomposition')",
            ("ev-" + uuid.uuid4().hex[:12], hid, str(f)[:500], now),
        )
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, before_state, "
        "after_state, rationale_concise) VALUES (?,?,'researcher','hypothesis',?,"
        "'author_from_event_decomp',NULL,'scored',?)",
        ("AUDIT-" + now.replace(":", "").replace("-", "") + "-" + hid[:20], now, hid,
         f"{cand['ticker']} {cand['move']:+.1f}% on {cand['date']}: {(res.get('priced_in') or '')[:300]}"),
    )
    return hid


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max", type=int, default=3, help="max movers to decompose this run")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--ticker", help="only this ticker (testing)")
    args = p.parse_args()

    import sqlite3
    conn = sqlite3.connect(DB_PATH, timeout=30)  # busy-wait: passes write concurrently (2026-07-23 lock crash)
    conn.row_factory = sqlite3.Row
    cands = find_candidates(conn, args.max, args.ticker)
    out = []
    for c in cands:
        res = decompose(conn, c)
        if not res:
            out.append({**c, "error": "no_decomposition"})
            continue
        rec = {**c, "stance": res.get("stance"), "conviction": res.get("conviction"),
               "first_order": res.get("first_order"), "second_order": res.get("second_order"),
               "priced_in": res.get("priced_in"), "falsifier": res.get("falsifier"),
               "thesis": res.get("thesis")}
        if not args.dry_run:
            hid = write_hypothesis(conn, c, res)
            rec["hypothesis_id"] = hid  # None = no_trade / not written
        out.append(rec)
    if not args.dry_run:
        conn.commit()
    print(json.dumps({"decomposed": len([o for o in out if "stance" in o]),
                      "candidates": len(cands), "dry_run": bool(args.dry_run),
                      "model": MODEL, "generated_at": _now(), "results": out}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
